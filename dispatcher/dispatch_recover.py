#!/usr/bin/env python3
"""dispatcher/dispatch_recover.py — bounded recovery ladder for a stalled dispatcher
(wq#1410, research/NIGHTLY_DISPATCH_RESILIENCE.md §3.3).

dispatch_health.py already detects a stalled dispatcher correctly and pages — the gap
this closes is that nothing used to ACT on the page (aq#794 sat open, count=145, since
08-23; bin/aq_escalate.py only escalated source='probe' rows). This module is the
actor: `dispatch_health.check_health()` calls `advance()` every tick the alarm
condition holds and `reset()` the first tick a pass is healthy again.

Ladder (one rung per health-check tick, never re-run, never looped):
  rung 1  harness-preflight --all   (fixes the stale-stamp class, §1.4)
  rung 2  restart the dispatch timer + kick the service once (fixes a wedged unit)
  rung 3  JSON-parse config.json and every permission-profiles/*.json, name the
          broken file (fixes nothing — makes the cause legible on the page)
After rung 3, if the NEXT tick is still unhealthy: stop advancing, file one
fleet-fires ticket (idempotent per incident via recover_state.json) and page urgent.
Never touches DISPATCH_DISARM. A healthy pass at any point resets the ladder, sends
one 'dispatcher recovered' ntfy (only if the ladder had actually progressed), and
resolves any open '%DISPATCHER STALLED%' alerts.db row so aq_escalate's count is
scoped to this incident, not lifetime (bin/aq_escalate.py's dispatcher-stall rule,
same ticket).

Rollback: set FOREMAN_RECOVER_DISABLE=1 — every public entry point becomes a no-op.

CLI:
  dispatch_recover.py status     # print current ladder state (zero side effects)
  dispatch_recover.py selftest   # wq#1410 §3.3.5: synthetic stall in dry-run,
                                  # asserts the classifier + rung 1 dry-run behave;
                                  # exits nonzero (paged via the unit's OnFailure=)
                                  # if the selftest itself is broken
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
FOREMAN_DIR = os.path.dirname(DISPATCHER_DIR)

STATE_PATH = os.environ.get("FOREMAN_RECOVER_STATE",
                            os.path.join(DISPATCHER_DIR, "runtime", "recover_state.json"))
HARNESS_PREFLIGHT_BIN = os.environ.get(
    "FOREMAN_HARNESS_PREFLIGHT_BIN",
    os.path.join(FOREMAN_DIR, "harness", "harness-preflight"))
CONFIG_PATH = os.environ.get("FOREMAN_DISPATCH_CONFIG",
                             os.path.join(DISPATCHER_DIR, "config.json"))
PROFILES_DIR = os.environ.get(
    "FOREMAN_PERMISSION_PROFILES_DIR",
    os.path.join(FOREMAN_DIR, "harness", "permission-profiles"))
SYSTEMCTL_BIN = os.environ.get("FOREMAN_SYSTEMCTL_BIN", "systemctl")
NTFY_BIN = os.environ.get("FOREMAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")
WQ_BIN = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")
AQ_DB = os.environ.get("AQ_DB", "/home/lumen/agents/alert-queue/alerts.db")

DISABLE_ENV = "FOREMAN_RECOVER_DISABLE"

RUNGS = ("preflight", "restart_unit", "diagnose_config")
MAX_RUNG = len(RUNGS)

FLEET_FIRES_TITLE = "[fleet-fires] dispatcher stalled, recovery ladder exhausted"
STALL_ALERT_TITLE_LIKE = "%DISPATCHER STALLED%"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _disabled():
    return os.environ.get(DISABLE_ENV) == "1"


def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st):
    d = os.path.dirname(STATE_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=2)
    os.replace(tmp, STATE_PATH)


def _ntfy(msg, priority="urgent", title="[DISPATCH] dispatcher recovery"):
    try:
        subprocess.run([NTFY_BIN, "-t", "lumen-alerts", "-p", priority, "-T", title,
                        "-g", "rotating_light", msg],
                       capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        pass


# ---- rungs --------------------------------------------------------------------------

def _rung_preflight(dry_run):
    """rung 1: harness-preflight --all — fixes the stale-stamp class (§1.4)."""
    if dry_run:
        return True, "dry-run: would run harness-preflight --all"
    try:
        r = subprocess.run([sys.executable, HARNESS_PREFLIGHT_BIN, "--all"],
                           capture_output=True, text=True, timeout=580)
        ok = r.returncode == 0
        detail = ((r.stdout or "") + (r.stderr or ""))[-800:]
        return ok, detail.strip() or f"exit={r.returncode}"
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"harness-preflight --all failed to run: {e!r}"


def _rung_restart_unit(dry_run):
    """rung 2: restart the timer + kick the service once — fixes a wedged unit."""
    if dry_run:
        return True, "dry-run: would restart lumen-foreman-dispatch.timer + start .service"
    try:
        r1 = subprocess.run([SYSTEMCTL_BIN, "restart", "lumen-foreman-dispatch.timer"],
                           capture_output=True, text=True, timeout=30)
        r2 = subprocess.run([SYSTEMCTL_BIN, "start", "lumen-foreman-dispatch.service"],
                           capture_output=True, text=True, timeout=30)
        ok = r1.returncode == 0 and r2.returncode == 0
        detail = (f"timer restart exit={r1.returncode} {r1.stderr.strip()[:200]}; "
                  f"service start exit={r2.returncode} {r2.stderr.strip()[:200]}")
        return ok, detail
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"systemctl calls failed: {e!r}"


def _diagnose_targets():
    targets = [CONFIG_PATH]
    if os.path.isdir(PROFILES_DIR):
        for root, _dirs, files in os.walk(PROFILES_DIR):
            for fn in sorted(files):
                if fn.endswith(".json"):
                    targets.append(os.path.join(root, fn))
    return targets


def _rung_diagnose_config(dry_run):
    """rung 3: never fixes anything — names the broken file so the page carries the
    root cause instead of just 'still stalled'. Checks config.json and every
    permission-profiles/*.json."""
    broken = []
    targets = _diagnose_targets()
    for path in targets:
        try:
            with open(path) as fh:
                json.load(fh)
        except OSError as e:
            broken.append(f"{path}: unreadable ({e.strerror or e})")
        except json.JSONDecodeError as e:
            broken.append(f"{path}: JSON error at line {e.lineno} col {e.colno}: {e.msg}")
    if broken:
        return False, "broken file(s): " + "; ".join(broken)
    return True, (f"{len(targets)} file(s) parse clean (config.json and every "
                  "permission-profiles/*.json are readable — the stall is elsewhere)")


_RUNG_FNS = {"preflight": _rung_preflight, "restart_unit": _rung_restart_unit,
             "diagnose_config": _rung_diagnose_config}


# ---- fleet-fires ticket + alert resolution -------------------------------------------

def _file_fleet_fires_ticket(st, dry_run):
    if dry_run:
        return None
    history = json.dumps(st.get("history", []), indent=2)[:1500]
    detail = (
        "Auto-filed by dispatch_recover.py (wq#1410) after the bounded 3-rung "
        "recovery ladder ran with no healthy pass following.\n\n"
        f"Incident started: {st.get('incident_started_ts')}\n"
        f"Ladder history:\n{history}\n\n"
        "RUNBOOK: dispatcher/dispatch_recover.py status; dispatcher.log for the "
        "stalled pass shape; runtime/recover_state.json; wq#1410\n\n"
        "Acceptance: dispatcher backlog moving again (spawned>0 or dispatchable=0 on "
        "the next pass), this ticket closed. reset() resolves the DISPATCHER STALLED "
        "alerts.db row itself the moment a healthy pass lands — do not resolve it by "
        "hand."
    )
    cmd = [WQ_BIN, "add", "--lane", "fleet-fires", "--pri", "0",
           "--title", FLEET_FIRES_TITLE, "--detail", detail]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"dispatch_recover: fleet-fires ticket seed EXC: {e}", file=sys.stderr)
        return None
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"added task (\d+)", out) or re.search(r"DUPLICATE .* task (\d+)", out)
    if m:
        return int(m.group(1))
    print(f"dispatch_recover: fleet-fires ticket seed FAILED: {out.strip()[:200]}",
          file=sys.stderr)
    return None


def _resolve_stall_alerts():
    """Closes any open '%DISPATCHER STALLED%' alerts.db row so aq_escalate's
    count-since-open is scoped to one incident, not lifetime (aq#794 sat at count=145
    since 08-23 precisely because nothing ever did this). Best-effort: a missed
    resolve costs one stale count on the next incident, never silence."""
    try:
        import sqlite3
        c = sqlite3.connect(AQ_DB, timeout=10)
        c.execute("UPDATE alerts SET status='resolved', last_ts=? "
                  "WHERE title LIKE ? AND status='open'",
                  (_now(), STALL_ALERT_TITLE_LIKE))
        c.commit()
        c.close()
    except Exception as e:  # noqa: BLE001 — never let this block a real recovery
        print(f"dispatch_recover: alert resolve failed (non-fatal): {e!r}", file=sys.stderr)


# ---- public entry points --------------------------------------------------------------

def advance(dry_run=False):
    """Called once per health-check tick while the alarm condition holds. Runs the
    next rung (bounded at MAX_RUNG, one rung per call via state['rung']). After
    MAX_RUNG rungs with no recovery, stops advancing, files the fleet-fires ticket
    once, and pages urgent. Never loops back to rung 1."""
    # dry_run (dispatch_health.py's --dry-run / selftest) must be exactly as
    # side-effect-free as the sensor's own dry-run: read the current state and
    # report what WOULD happen next, but never persist, ntfy, or file a ticket.
    if _disabled():
        return {"disabled": True}
    st = load_state()
    rung_n = st.get("rung", 0)

    if rung_n >= MAX_RUNG:
        if st.get("escalated"):
            return {"rung": rung_n, "escalated": True,
                    "ticket": st.get("escalated_ticket"),
                    "note": "ladder exhausted, already escalated — waiting for reset()"}
        ticket_id = _file_fleet_fires_ticket(st, dry_run)
        if dry_run:
            return {"rung": rung_n, "escalated": True, "ticket": ticket_id}
        st["escalated"] = True
        st["escalated_ticket"] = ticket_id
        st["escalated_ts"] = _now()
        save_state(st)
        _ntfy(f"Dispatcher recovery ladder exhausted ({MAX_RUNG} rungs run), "
              f"still stalled. fleet-fires wq#{ticket_id}.",
              title="[DISPATCH] DISPATCHER STALLED — ladder exhausted")
        return {"rung": rung_n, "escalated": True, "ticket": ticket_id}

    rung_n += 1
    name = RUNGS[rung_n - 1]
    ok, detail = _RUNG_FNS[name](dry_run)
    result = {"rung": rung_n, "name": name, "ok": ok, "detail": detail, "ts": _now()}
    if dry_run:
        return {"rung": rung_n, "name": name, "ok": ok, "detail": detail, "escalated": False}
    st.setdefault("incident_started_ts", _now())
    st["rung"] = rung_n
    st["last_rung_result"] = result
    st.setdefault("history", []).append(result)
    save_state(st)
    return {"rung": rung_n, "name": name, "ok": ok, "detail": detail, "escalated": False}


def reset(dry_run=False, note="dispatcher recovered"):
    """Called the first tick a scored pass is healthy again. Resets the ladder,
    sends one 'dispatcher recovered' ntfy if the ladder had actually progressed
    (a healthy pass with no prior incident is silent — that's just normal
    operation), and resolves the open DISPATCHER STALLED alerts.db row."""
    if _disabled():
        return {"disabled": True}
    st = load_state()
    had_progress = st.get("rung", 0) > 0
    if had_progress and not dry_run:
        _ntfy(note, priority="default", title="[DISPATCH] dispatcher recovered")
        _resolve_stall_alerts()
    if not dry_run:
        save_state({})
    return {"reset": True, "had_progress": had_progress}


def status():
    return load_state()


# ---- CLI --------------------------------------------------------------------------

def cmd_status(_args):
    st = status()
    print(json.dumps(st, indent=2) if st else json.dumps({"idle": True}))
    return 0


def cmd_selftest(_args):
    """wq#1410 §3.3.5: write a synthetic 2-pass stall to a temp log, run the real
    classifier + alarm path in dry-run, and assert it fires; run rung 1 in dry-run
    and assert it reports ok. Exits nonzero (and prints why) on any failed
    assertion — this selftest's own unit pages via OnFailure= on a nonzero exit, so
    a broken selftest is loud, not silent."""
    import tempfile
    sys.path.insert(0, DISPATCHER_DIR)
    import dispatch_health  # noqa: E402  (needs DISPATCHER_DIR on sys.path first)

    ok = True
    with tempfile.TemporaryDirectory() as td:
        log_path = os.path.join(td, "dispatcher.log")
        with open(log_path, "w") as f:
            for i in range(2):
                pid = f"fdisp-selftest{i}"
                f.write(json.dumps({"ts": _now(), "pass_id": pid,
                                    "event": "pass_start", "dry_run": False}) + "\n")
                f.write(json.dumps({"ts": _now(), "pass_id": pid, "event": "pass_end",
                                    "spawned": 0, "candidates": 5,
                                    "dispatchable": 5}) + "\n")
        fired, detail = dispatch_health.check_health(log_path, threshold=2, dry_run=True)
        if not fired:
            print(f"SELFTEST FAIL: synthetic stall did not fire: {detail}")
            ok = False
        else:
            print("SELFTEST: synthetic stall correctly WOULD ALERT")

    rung_ok, rung_detail = _rung_preflight(dry_run=True)
    if not rung_ok:
        print(f"SELFTEST FAIL: rung 1 dry-run reported failure: {rung_detail}")
        ok = False
    else:
        print(f"SELFTEST: rung 1 dry-run OK: {rung_detail}")

    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Bounded recovery ladder for a stalled dispatcher (wq#1410)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="print current ladder state, zero side effects")
    sub.add_parser("selftest", help="synthetic stall in dry-run; nonzero exit on failure")
    a = ap.parse_args(argv)

    if a.cmd == "status":
        return cmd_status(a)
    if a.cmd == "selftest":
        return cmd_selftest(a)
    return 1


if __name__ == "__main__":
    sys.exit(main())
