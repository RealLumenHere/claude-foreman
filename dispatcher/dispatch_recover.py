#!/usr/bin/env python3
"""dispatcher/dispatch_recover.py — dispatcher stall recovery ladder (wq#1410).

research/NIGHTLY_DISPATCH_RESILIENCE.md §1.2 + §3.3: dispatch_health.py (wq#825)
already detects a stalled dispatcher correctly and pages URGENT — but nothing acted
on the page (alerts.db row 794: count=145, status=open since 08-23). This module is
the actor dispatch_health.py was missing: a BOUNDED ladder, advanced one rung per
health-check tick while the alarm condition holds, never looping back, never
touching DISPATCH_DISARM.

Rungs (state in runtime/recover_state.json):
  1. `harness-preflight --all` every migrated repo — fixes a stale/broken stamp
     (§1.4/§1.3), the most common real-world stall cause.
  2. `systemctl restart` the dispatch timer + `start` the service — fixes a wedged
     unit or a timer that stopped firing.
  3. json-parse config.json and every permission-profiles/*.json (incl. repos.d/)
     and name the first file that fails to parse (§1.5 :891 class). Fixes nothing;
     it makes the next human step a `path:line`, not a grep session.
  after rung 3, still unhealthy: STOP. Never re-run rung 1. Page urgent once; the
  ticket itself is filed by aq_escalate's DISPATCHER STALLED rule (bin/aq_escalate.py,
  a separate repo — see that file's second rule), which owns count-based escalation
  and idempotent ticket seeding.

Any healthy pass resets the ladder to rung 0 and sends one `default` "dispatcher
recovered" ntfy — but only if the ladder had actually advanced (no-op spam on an
ordinary healthy pass with no prior incident).

Rollback: FOREMAN_RECOVER_DISABLE=1 makes advance() a no-op (report only). This
module never disarms DISPATCH_DISARM and never writes it.

CLI:
  dispatch_recover.py advance [--dry-run]   # run one rung; called by dispatch_health.py
  dispatch_recover.py reset [--dry-run]     # called by dispatch_health.py on healthy
  dispatch_recover.py status                # print current ladder state
  dispatch_recover.py selftest              # synthetic stall -> classify -> dry-run
                                             # rung 1 -> assert the page would fire
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
HARNESS_DIR = os.path.join(os.path.dirname(DISPATCHER_DIR), "harness")
PROFILES_DIR = os.path.join(HARNESS_DIR, "permission-profiles")
CONFIG_PATH = os.path.join(DISPATCHER_DIR, "config.json")
HARNESS_PREFLIGHT_CLI = os.path.join(HARNESS_DIR, "harness-preflight")

sys.path.insert(0, DISPATCHER_DIR)

STATE_PATH = os.environ.get("FOREMAN_RECOVER_STATE",
                            os.path.join(DISPATCHER_DIR, "runtime", "recover_state.json"))
NTFY_BIN = os.environ.get("FOREMAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")
# wq#1410 §2.2 item 1: the bare "claude" name resolves under an interactive shell's
# PATH but not the dispatch unit's — the same class of bug this ladder exists to
# recover from. Rung 1 always uses an absolute path so it can't reintroduce it.
CLAUDE_BIN = os.environ.get("FOREMAN_CLAUDE_BIN", "/home/lumen/.npm-global/bin/claude")
DISPATCH_TIMER = os.environ.get("FOREMAN_DISPATCH_TIMER", "lumen-foreman-dispatch.timer")
DISPATCH_SERVICE = os.environ.get("FOREMAN_DISPATCH_SERVICE", "lumen-foreman-dispatch.service")
DISARM_PATH = os.environ.get("FOREMAN_DISPATCH_DISARM",
                             "/home/lumen/autonomy/DISPATCH_DISARM")
DISABLE_ENV = "FOREMAN_RECOVER_DISABLE"
# wq#1410 §3.3.3: resolved here (not left open forever like row aq#794, count=145
# since 08-23) so bin/aq_escalate.py's DISPATCHER STALLED rule sees a per-incident
# count on the next open row, the same "resolve -> next row starts fresh" pattern
# aq_escalate already uses for probe alerts.
AQ_DB = os.environ.get("AQ_DB", "/home/lumen/agents/alert-queue/alerts.db")
STALLED_TITLE_LIKE = "%DISPATCHER STALLED%"

MAX_RUNG = 3
STALL_TAG_TITLE = "[DISPATCH] recovery ladder exhausted"
RECOVERED_TITLE = "[DISPATCH] dispatcher recovered"


def _utcnow():
    return datetime.now(timezone.utc)


def _ntfy(msg, priority="default", title=RECOVERED_TITLE):
    try:
        subprocess.run([NTFY_BIN, "-t", "lumen-alerts", "-p", priority, "-T", title, msg],
                       capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pass


# ---- state.json: {"rung": N, "stopped": bool, "started_at": iso, "history": [...]} ---

def _fresh_state():
    return {"rung": 0, "stopped": False, "started_at": None, "history": []}


def load_state():
    try:
        with open(STATE_PATH) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return _fresh_state()
    if not isinstance(st, dict):
        return _fresh_state()
    fresh = _fresh_state()
    fresh["rung"] = int(st.get("rung") or 0)
    fresh["stopped"] = bool(st.get("stopped", False))
    fresh["started_at"] = st.get("started_at")
    hist = st.get("history")
    fresh["history"] = hist if isinstance(hist, list) else []
    return fresh


def save_state(st):
    d = os.path.dirname(STATE_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATE_PATH)


# ---- rungs -----------------------------------------------------------------------------

def rung1_preflight(dry_run):
    """harness-preflight --all every migrated repo (fixes the stamp class, §1.4)."""
    cmd = [sys.executable, HARNESS_PREFLIGHT_CLI, "--all", "--claude-bin", CLAUDE_BIN]
    if dry_run:
        return True, "DRY-RUN would run: " + " ".join(cmd)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=580)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"harness-preflight --all failed to run: {e!r}"
    ok = p.returncode == 0
    tail = (p.stdout + p.stderr).strip()[-1500:]
    return ok, f"harness-preflight --all rc={p.returncode}\n{tail}"


def rung2_restart(dry_run):
    """restart the dispatch timer + start the service (fixes a wedged unit, §1.5)."""
    cmds = [["systemctl", "restart", DISPATCH_TIMER],
            ["systemctl", "start", DISPATCH_SERVICE]]
    if dry_run:
        return True, "DRY-RUN would run: " + " && ".join(" ".join(c) for c in cmds)
    details = []
    ok = True
    for c in cmds:
        try:
            p = subprocess.run(c, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            ok = False
            details.append(f"{' '.join(c)} -> EXC {e!r}")
            continue
        if p.returncode != 0:
            ok = False
        details.append(f"{' '.join(c)} -> rc={p.returncode} {p.stderr.strip()[:200]}")
    return ok, "; ".join(details)


def _json_files_to_check():
    files = [CONFIG_PATH]
    for base in (PROFILES_DIR, os.path.join(PROFILES_DIR, "repos.d")):
        try:
            names = sorted(f for f in os.listdir(base) if f.endswith(".json"))
        except OSError:
            continue
        files.extend(os.path.join(base, n) for n in names)
    return files


def rung3_diagnose(dry_run):
    """json-parse config.json + every permission-profiles/*.json (incl. repos.d/) and
    name the first unparseable file:line (§1.5 :891 class). Fixes nothing on purpose —
    a JSON syntax error needs a human edit; this just turns a grep session into a
    file:line the page already names."""
    checked = _json_files_to_check()
    broken = []
    for path in checked:
        try:
            with open(path) as f:
                json.load(f)
        except OSError as e:
            broken.append(f"{path}: unreadable ({e})")
        except json.JSONDecodeError as e:
            broken.append(f"{path}:{e.lineno}: {e.msg}")
    if dry_run:
        return (not broken), f"DRY-RUN checked {len(checked)} file(s); broken={broken}"
    if broken:
        return False, "broken JSON: " + "; ".join(broken)
    return True, f"all {len(checked)} JSON file(s) parse cleanly (names nothing to fix)"


RUNGS = {
    1: ("harness-preflight --all", rung1_preflight),
    2: ("restart dispatch timer+service", rung2_restart),
    3: ("diagnose config/profile JSON", rung3_diagnose),
}


# ---- ladder actor -----------------------------------------------------------------------

def advance(dry_run=False):
    """Run exactly one rung forward from the ladder's current position. Called once
    per dispatch_health.py tick while the alarm condition holds. Idempotent per tick:
    once stopped (post rung 3, still unhealthy) it reports and does nothing further
    until reset() by a healthy pass."""
    if os.environ.get(DISABLE_ENV) == "1":
        return {"action": "disabled", "rung": 0,
                "detail": f"{DISABLE_ENV}=1 — ladder not advanced"}

    st = load_state()
    if st["stopped"]:
        return {"action": "stopped", "rung": st["rung"],
                "detail": f"ladder already stopped at rung {st['rung']} — "
                          "waiting for a healthy pass to reset it"}

    if os.path.exists(DISARM_PATH):
        return {"action": "paused", "rung": st["rung"],
                "detail": f"DISPATCH_DISARM present ({DISARM_PATH}) — ladder paused, "
                          "not advancing (never touched by this module)"}

    next_rung = st["rung"] + 1
    if next_rung > MAX_RUNG:
        st["stopped"] = True
        if not dry_run:
            save_state(st)
        msg = (f"Recovery ladder exhausted after rung {MAX_RUNG} "
               f"({RUNGS[MAX_RUNG][0]}) — dispatcher still unhealthy. "
               "aq_escalate's DISPATCHER STALLED rule owns filing the fleet-fires "
               "ticket; this is the immediate page.")
        if dry_run:
            print(f"WOULD PAGE (ladder exhausted): {msg}")
        else:
            _ntfy(msg, priority="urgent", title=STALL_TAG_TITLE)
        return {"action": "exhausted", "rung": MAX_RUNG, "detail": msg}

    name, fn = RUNGS[next_rung]
    ok, detail = fn(dry_run)
    if not dry_run:
        if not st.get("started_at"):
            st["started_at"] = _utcnow().isoformat(timespec="seconds")
        st["rung"] = next_rung
        st["history"].append({"ts": _utcnow().isoformat(timespec="seconds"),
                              "rung": next_rung, "name": name, "ok": ok,
                              "detail": detail[:2000]})
        save_state(st)
    return {"action": "ran_rung", "rung": next_rung, "name": name, "ok": ok, "detail": detail}


def _resolve_stalled_alert_rows():
    """Close any open DISPATCHER STALLED alerts.db row(s) so the next stall's page
    starts a fresh row with a per-incident count, not row aq#794's lifetime 145.
    Fail-open: any DB problem is swallowed, never raised into reset()'s caller."""
    conn = None
    try:
        conn = sqlite3.connect(AQ_DB, timeout=15)
        with conn:
            cur = conn.execute(
                "UPDATE alerts SET status='resolved', resolved_ts=? "
                "WHERE title LIKE ? AND status='open'",
                (_utcnow().isoformat(timespec="seconds"), STALLED_TITLE_LIKE))
            return cur.rowcount
    except sqlite3.Error:
        return 0
    finally:
        if conn is not None:
            conn.close()


def reset(dry_run=False, reason="dispatcher recovered"):
    """A healthy pass resets the ladder. Only pages if the ladder had actually
    advanced or stopped — an ordinary healthy pass with no prior incident is silent."""
    st = load_state()
    was_active = st["rung"] > 0 or st["stopped"]
    if was_active:
        msg = f"{reason} — recovery ladder reset (was at rung {st['rung']})."
        if dry_run:
            print(f"WOULD NTFY (recovered): {msg}")
        else:
            _ntfy(msg, priority="default", title=RECOVERED_TITLE)
            _resolve_stalled_alert_rows()
    if not dry_run:
        save_state(_fresh_state())
    return was_active


# ---- selftest (§3.3.5): wired into the weekly gate elsewhere; here it's a plain,
# no-side-effect check runnable any time. ------------------------------------------------

def selftest():
    """Synthetic 2-pass stall -> dispatch_health.classify_passes/check_health (dry-run)
    -> assert the page WOULD fire -> advance() in dry-run and assert rung 1 runs clean.
    Never touches the real log, state file, or ntfy. Returns True on pass."""
    import dispatch_health

    ok = True
    with tempfile.TemporaryDirectory() as td:
        log_path = os.path.join(td, "dispatcher.log")
        now = _utcnow()
        lines = []
        for i in (1, 2):
            pid = f"fdisp-selftest{i}"
            lines.append(json.dumps({"ts": now.isoformat(timespec="seconds"),
                                     "pass_id": pid, "event": "pass_start",
                                     "dry_run": False}))
            lines.append(json.dumps({"ts": now.isoformat(timespec="seconds"),
                                     "pass_id": pid, "event": "pass_end",
                                     "candidates": 12, "dispatchable": 5, "spawned": 0}))
        with open(log_path, "w") as f:
            f.write("\n".join(lines) + "\n")

        events = dispatch_health.parse_log_tail(log_path, dispatch_health.TAIL_LINES)
        results = dispatch_health.classify_passes(events)
        verdicts = [v for _pid, _ts, v, _r in results]
        if verdicts != ["unhealthy", "unhealthy"]:
            print(f"SELFTEST FAIL: expected 2x unhealthy, classified {verdicts}")
            ok = False

        fired, detail = dispatch_health.check_health(log_path, threshold=2, dry_run=True)
        if not fired:
            print(f"SELFTEST FAIL: dry-run check_health did not fire: {detail}")
            ok = False
        else:
            print(f"SELFTEST: page would fire — {detail.splitlines()[0]}")

    state_path = os.path.join(tempfile.mkdtemp(), "recover_state.json")
    global STATE_PATH
    real_state_path = STATE_PATH
    STATE_PATH = state_path
    try:
        result = advance(dry_run=True)
    finally:
        STATE_PATH = real_state_path
    if result["action"] != "ran_rung" or result["rung"] != 1 or not result["ok"]:
        print(f"SELFTEST FAIL: dry-run advance() did not run a clean rung 1: {result}")
        ok = False
    else:
        print(f"SELFTEST: rung 1 dry-run ok — {result['detail'].splitlines()[0]}")

    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description="Dispatcher stall recovery ladder (wq#1410)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("advance", help="run one rung forward if the ladder isn't stopped")
    a.add_argument("--dry-run", action="store_true")

    r = sub.add_parser("reset", help="reset the ladder (a healthy pass calls this)")
    r.add_argument("--dry-run", action="store_true")

    sub.add_parser("status", help="print the current ladder state")
    sub.add_parser("selftest", help="synthetic stall -> classify -> dry-run rung 1")

    args = ap.parse_args(argv)

    if args.cmd == "advance":
        result = advance(dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("ok", True) else 1)
    elif args.cmd == "reset":
        was_active = reset(dry_run=args.dry_run)
        print(json.dumps({"reset": True, "was_active": was_active}))
    elif args.cmd == "status":
        print(json.dumps(load_state(), indent=2))
    elif args.cmd == "selftest":
        sys.exit(0 if selftest() else 1)


if __name__ == "__main__":
    main()
