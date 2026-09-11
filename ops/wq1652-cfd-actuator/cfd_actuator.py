#!/usr/bin/env python3
"""cfd-actuator — the single cloudflared restart owner (wq#1652).

FLEET_SERVICE_ATLAS.md §3.2 (wq#1627): three uncoordinated restarters of
cloudflared — lumen-self-healer (watch table), lumen-public-guardian (heal
ladder), wadjet-ha-monitor (failover) — and Commander rides that tunnel, so an
ill-timed restart is the documented SIGKILL-mid-script outage vector. This CLI
is the ONE code path allowed to bounce cloudflared. The three detectors stop
calling systemctl and instead file a request:

    cfd-actuator request --from <caller> --kind restart|reload --reason "why"

which opens an aq row (source=cfd-restart-req, key=<kind>:<caller>). A 60 s
timer runs `cfd-actuator drain`, which serializes everything:

  holddown  — `holddown check cloudflared` ACTIVE means a session owns the
              unit (wq#967 contract): never act, leave rows open.
  slock     — scope `cloudflared` acquired before acting (never piped — the
              exit status is the safety signal, wq#532).
  cooldown  — at most one RESTART per CFD_COOLDOWN_S (default 300 s). A
              request landing inside the window is DEFERRED, never dropped:
              it is served on the first drain after expiry. One Commander
              interruption per 5 min, worst case.
  coalesce  — N open rows are satisfied by one action; restart supersedes
              reload (a restart re-reads config anyway).

reload (SIGHUP, graceful config re-read — wadjet's origin flip) is exempt
from the cooldown; only the disruptive restart is rate-limited. The restart
itself is the exact detached form from ISSUE-LOG #46 / guardian's
HEAL_ALLOWLIST — byte-identical to the sudoers rule, no --unit:

    sudo /usr/bin/systemd-run --collect /usr/bin/systemctl restart cloudflared.service

If the drain timer dies, requests pile up open: `request` pages urgent once
CFD_ESCALATE_OPEN_ROWS (default 5) rows are already open, so a dead actuator
is loud from the requester side, not silent.

Env (tests/extension): CFD_AQ_DIR, CFD_STATE_DIR, CFD_HOLDDOWN_BIN,
CFD_SLOCK_BIN, CFD_NTFY_BIN, CFD_COOLDOWN_S, CFD_ESCALATE_OPEN_ROWS,
CFD_RESTART_CMD / CFD_RELOAD_CMD / CFD_ISACTIVE_CMD (JSON argv overrides).
"""
import argparse
import json
import os
import subprocess
import sys
import time

SOURCE = "cfd-restart-req"
AQ_DIR = os.environ.get("CFD_AQ_DIR", "/home/lumen/agents/alert-queue")
STATE_DIR = os.environ.get("CFD_STATE_DIR", "/home/lumen/state/cfd-actuator")
HOLDDOWN_BIN = os.environ.get("CFD_HOLDDOWN_BIN", "/home/lumen/bin/holddown")
SLOCK_BIN = os.environ.get("CFD_SLOCK_BIN", "/home/lumen/bin/slock")
NTFY_BIN = os.environ.get("CFD_NTFY_BIN", "/home/lumen/bin/ntfy-send")
COOLDOWN_S = int(os.environ.get("CFD_COOLDOWN_S", "300"))
ESCALATE_OPEN_ROWS = int(os.environ.get("CFD_ESCALATE_OPEN_ROWS", "5"))
HOLDDOWN_ESCALATE_S = int(os.environ.get("CFD_HOLDDOWN_ESCALATE_S", "1800"))

# Byte-identical to the sudoers rule (ISSUE-LOG #46: no --unit, no wildcards).
RESTART_CMD = json.loads(os.environ.get("CFD_RESTART_CMD", json.dumps(
    ["sudo", "/usr/bin/systemd-run", "--collect", "/usr/bin/systemctl",
     "restart", "cloudflared.service"])))
RELOAD_CMD = json.loads(os.environ.get("CFD_RELOAD_CMD", json.dumps(
    ["sudo", "/usr/bin/systemctl", "kill", "-s", "HUP", "cloudflared.service"])))
ISACTIVE_CMD = json.loads(os.environ.get("CFD_ISACTIVE_CMD", json.dumps(
    ["/usr/bin/systemctl", "is-active", "--quiet", "cloudflared.service"])))
STATE_FILE = os.path.join(STATE_DIR, "state.json")


def aq_mod():
    sys.path.insert(0, AQ_DIR)
    import aq
    return aq


def ntfy(title, msg, prio="high"):
    try:
        subprocess.run([NTFY_BIN, "-p", prio, "-T", title, msg],
                       capture_output=True, text=True, timeout=10)
    except Exception:
        pass


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_FILE)


def open_rows(aq):
    c = aq.db()
    rows = c.execute(
        "SELECT id, key FROM alerts WHERE source=? AND status='open'",
        (SOURCE,)).fetchall()
    c.close()
    return rows


def resolve_rows(aq, ids, note):
    c = aq.db()
    ts = aq.now()
    for rid in ids:
        c.execute("UPDATE alerts SET status='resolved', note=?, resolved_ts=? "
                  "WHERE id=? AND status='open'", (note, ts, rid))
    c.commit()
    c.close()


def cmd_request(a):
    if a.kind not in ("restart", "reload"):
        sys.exit(f"cfd-actuator: bad --kind {a.kind!r} (restart|reload)")
    aq = aq_mod()
    already = open_rows(aq)
    aq.ingest(source=SOURCE, key=f"{a.kind}:{a.caller}",
              title=f"cloudflared {a.kind} requested by {a.caller}",
              message=a.reason, priority="high")
    print(f"cfd-actuator: {a.kind} request filed for {a.caller} "
          f"({len(already)} already open)")
    if len(already) >= ESCALATE_OPEN_ROWS:
        # The drain timer is not draining — that is the real emergency, and a
        # dead drainer cannot page for itself.
        ntfy("CFD-ACTUATOR NOT DRAINING",
             f"{len(already)} open {SOURCE} rows and {a.caller} just filed "
             f"another ({a.kind}: {a.reason}). Check lumen-cfd-actuator.timer.",
             prio="urgent")
    return 0


def holddown_active():
    r = subprocess.run([HOLDDOWN_BIN, "check", "cloudflared"],
                       capture_output=True, text=True, timeout=15)
    return r.returncode == 0


def slock_acquire():
    # Never piped: the exit status is the grant/deny signal (wq#532).
    r = subprocess.run([SLOCK_BIN, "acquire", "cloudflared", "--ttl", "5m",
                        "--holder", "cfd-actuator",
                        "--note", "wq#1652 single cloudflared actuator"],
                       capture_output=True, text=True, timeout=15)
    return r.returncode == 0


def slock_release():
    subprocess.run([SLOCK_BIN, "release", "cloudflared",
                    "--holder", "cfd-actuator"],
                   capture_output=True, text=True, timeout=15)


def run_action(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return r.returncode == 0


def tunnel_active(poll_s):
    deadline = time.time() + poll_s
    while time.time() < deadline:
        r = subprocess.run(ISACTIVE_CMD, capture_output=True, timeout=15)
        if r.returncode == 0:
            return True
        time.sleep(2)
    return False


def cmd_drain(a):
    aq = aq_mod()
    rows = open_rows(aq)
    st = load_state()
    if not rows:
        st.pop("pending_since", None)
        st.pop("holddown_escalated", None)
        save_state(st)
        return 0
    now = time.time()
    st.setdefault("pending_since", now)

    if holddown_active():
        # A session owns the unit; requests wait. Page once if the hold has
        # starved requests for too long.
        if (now - st["pending_since"] > HOLDDOWN_ESCALATE_S
                and not st.get("holddown_escalated")):
            ntfy("cfd-actuator: holddown starving restart requests",
                 f"{len(rows)} open {SOURCE} rows waiting "
                 f"{int(now - st['pending_since'])}s behind an active "
                 "cloudflared holddown.", prio="urgent")
            st["holddown_escalated"] = True
        save_state(st)
        print(f"cfd-actuator: holddown active, {len(rows)} request(s) deferred")
        return 0

    restart_rows = [(i, k) for i, k in rows if k.split(":", 1)[0] == "restart"]
    reload_rows = [(i, k) for i, k in rows if k.split(":", 1)[0] != "restart"]
    want_restart = bool(restart_rows)
    if want_restart:
        since = now - st.get("last_restart_ts", 0)
        if since < COOLDOWN_S:
            if not reload_rows:
                save_state(st)
                print(f"cfd-actuator: cooldown ({int(COOLDOWN_S - since)}s "
                      f"left), {len(rows)} request(s) deferred")
                return 0
            # Serve the graceful reloads now; the restart keeps waiting.
            want_restart = False

    if not slock_acquire():
        save_state(st)
        print("cfd-actuator: slock cloudflared denied, deferring")
        return 0
    try:
        if want_restart:
            served = rows  # a restart re-reads config: supersedes reloads
            ok = run_action(RESTART_CMD) and tunnel_active(
                int(os.environ.get("CFD_ACTIVE_POLL_S", "30")))
            action = "restart"
        else:
            served = reload_rows
            ok = run_action(RELOAD_CMD)
            action = "reload"
        callers = ", ".join(sorted(k for _, k in served))
        if ok:
            if want_restart:
                st["last_restart_ts"] = now
            st["consec_fails"] = 0
            if len(served) == len(rows):
                st.pop("pending_since", None)
                st.pop("holddown_escalated", None)
            resolve_rows(aq, [rid for rid, _ in served],
                         f"cfd-actuator: {action} done for [{callers}]")
            ntfy(f"cfd-actuator: cloudflared {action}",
                 f"served requests: {callers}", prio="default")
            print(f"cfd-actuator: {action} OK, resolved {len(served)} row(s)")
            return 0
        st["consec_fails"] = st.get("consec_fails", 0) + 1
        if st["consec_fails"] >= 3:
            ntfy("CFD-ACTUATOR FAILING",
                 f"cloudflared {action} failed {st['consec_fails']}x; "
                 f"{len(rows)} request(s) still open [{callers}]",
                 prio="urgent")
        print(f"cfd-actuator: {action} FAILED "
              f"(consec_fails={st['consec_fails']})")
        return 1
    finally:
        save_state(st)
        slock_release()


def main(argv=None):
    p = argparse.ArgumentParser(prog="cfd-actuator")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("request", help="file a restart/reload request (any caller)")
    s.add_argument("--from", dest="caller", required=True)
    s.add_argument("--kind", default="restart")
    s.add_argument("--reason", required=True)
    s.set_defaults(f=cmd_request)
    s = sub.add_parser("drain", help="owner path: serve open requests (timer only)")
    s.set_defaults(f=cmd_drain)
    a = p.parse_args(argv)
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
