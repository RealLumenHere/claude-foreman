#!/usr/bin/env python3
"""dispatcher_health.py — dispatcher-not-dispatching alarm (wq#825).

PROBLEM (observed 2026-08-22): night-watch looped 600+ times while EVERY
foreman_dispatcher.py pass died at stage=config (bad user-lane value). Nothing watches
the dispatcher's own HEALTH — only worker verdicts — so 10 hours passed with 0 spawns
and no page to Frank. A dispatcher that refuses to run is the highest-severity silent
failure: the systemd timer looks alive (it fires on schedule, the service unit even
exits 0 in the spawned=0-with-candidates case) while nothing is actually being
dispatched.

FIX: read foreman_dispatcher.py's own decision log (LOG_PATH, one JSON object per
line, tagged by pass_id — see that module's docstring) and flag a pass unhealthy when
it shows either signal called out in the wq#825 ticket:
  - an `event=error` line (config load failed, an unhandled exception, ...), or
  - a `pass_end` line with spawned=0 while candidates>0 (the pipeline ran clean but
    produced zero spawns despite work being available — a bug or a mis-set filter,
    not a crash, so the process exit code alone would never catch it).
`check_dispatcher_health()` counts back from the most recent pass while each is
unhealthy; CONSECUTIVE_THRESHOLD (default 2, matching the ticket's own test plan: "an
alarm fires within 2 cycles") consecutive unhealthy passes pages Frank via
notify_sinks.notify_critical (urgent priority, same dual-sink fan-out the circuit
breaker uses). A `halt` pass (kill_switch, lock_busy, circuit_breaker trip, ...) is
never itself unhealthy — those are either an intentional stop Frank already controls
directly (DISPATCH_DISARM) or already alarmed by circuit_breaker.py — so a halt breaks
an unhealthy streak rather than extending it.

This module is deliberately read-only and side-effect-free besides the ntfy + its own
small dedup ledger (STATE_PATH): it never touches DISPATCH_DISARM, never releases or
blocks a ticket. Wired into self_heal.py's existing 10-min pass (see that module) so
Frank doesn't have to separately enable a third systemd timer for this.

CLI:  dispatcher_health.py status           # current streak, zero side effects
      dispatcher_health.py check [--dry-run]  # real pass: may ntfy + update dedup state
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DISPATCHER_DIR)
import notify_sinks  # noqa: E402

FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")
# Same env var + default foreman_dispatcher.py itself reads — one source of truth for
# where the decision log lives, not a second config knob that could drift out of sync.
LOG_PATH = os.environ.get("FOREMAN_DISPATCHER_LOG",
                          os.path.join(FOREMAN_ROOT, "dispatcher.log"))
STATE_PATH = os.environ.get("FOREMAN_HEALTH_STATE",
                            os.path.join(FOREMAN_ROOT, "dispatcher_health", "state.json"))

CONSECUTIVE_THRESHOLD = int(os.environ.get("FOREMAN_HEALTH_CONSECUTIVE", "2"))
NTFY_DEDUP_HOURS = float(os.environ.get("FOREMAN_HEALTH_NTFY_DEDUP_H", "1"))


def _utcnow():
    return datetime.now(timezone.utc)


# ---- decision log -----------------------------------------------------------------------

def iter_passes(log_path=None):
    """[(pass_id, [event dict, ...]), ...] in file order (oldest first), one entry per
    distinct pass_id found in the dispatcher's decision log. A corrupt/partial line is
    skipped, not fatal — one bad line must never blind this to real signal. Missing log
    (dispatcher never ran, or a fresh install) yields []."""
    log_path = log_path if log_path is not None else LOG_PATH
    try:
        with open(log_path) as f:
            lines = f.readlines()
    except OSError:
        return []
    passes, order = {}, []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        pid = rec.get("pass_id")
        if not pid:
            continue
        if pid not in passes:
            passes[pid] = []
            order.append(pid)
        passes[pid].append(rec)
    return [(pid, passes[pid]) for pid in order]


def pass_is_unhealthy(events):
    """(unhealthy: bool, reason: str|None) for one pass's events, per the wq#825
    definition: an `error` event, or a `pass_end` with spawned=0 while candidates>0."""
    for rec in events:
        event = rec.get("event")
        if event == "error":
            return True, f"error stage={rec.get('stage', '?')}: {rec.get('reason', '')}"
        if event == "pass_end":
            spawned, candidates = rec.get("spawned"), rec.get("candidates")
            if spawned == 0 and isinstance(candidates, (int, float)) and candidates > 0:
                return True, f"spawned=0 with candidates={candidates}"
    return False, None


def recent_unhealthy_streak(log_path=None):
    """(streak_len, most_recent_pass_id, [reason, ...]) counting back from the most
    recent pass while each remains unhealthy; stops at the first healthy or halted
    pass. most_recent_pass_id is None when the log has no passes yet."""
    passes = iter_passes(log_path)
    if not passes:
        return 0, None, []
    passes.reverse()  # most recent first
    reasons = []
    for _pid, events in passes:
        unhealthy, reason = pass_is_unhealthy(events)
        if not unhealthy:
            break
        reasons.append(reason)
    return len(reasons), passes[0][0], reasons


# ---- dedup state.json: {"last_alarm_ts": iso, "last_alarm_pass_id": "..."} -------------

def load_state():
    try:
        with open(STATE_PATH) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {"last_alarm_ts": None, "last_alarm_pass_id": None}
    if not isinstance(st, dict):
        return {"last_alarm_ts": None, "last_alarm_pass_id": None}
    return {"last_alarm_ts": st.get("last_alarm_ts"),
            "last_alarm_pass_id": st.get("last_alarm_pass_id")}


def save_state(st):
    d = os.path.dirname(STATE_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATE_PATH)


def _should_ntfy(state, now):
    last_ts = state.get("last_alarm_ts")
    if not last_ts:
        return True
    try:
        last = datetime.fromisoformat(last_ts)
    except ValueError:
        return True
    return now - last >= timedelta(hours=NTFY_DEDUP_HOURS)


# ---- the check ----------------------------------------------------------------------------

def check_dispatcher_health(dry_run=False, now=None):
    """(alarmed: bool, info: dict). `alarmed` is True whenever the streak is at/over
    threshold, independent of whether this call actually sent a fresh ntfy (dedup /
    dry-run suppress the notification, never the truth in `info`)."""
    now = now if now is not None else _utcnow()
    streak, most_recent_pass_id, reasons = recent_unhealthy_streak()
    info = {"streak": streak, "threshold": CONSECUTIVE_THRESHOLD,
            "most_recent_pass_id": most_recent_pass_id, "reasons": reasons}
    if streak < CONSECUTIVE_THRESHOLD:
        return False, info

    state = load_state()
    if not _should_ntfy(state, now):
        info["deduped"] = True
        return True, info

    msg = (f"dispatcher NOT DISPATCHING: {streak} consecutive unhealthy passes "
           f"(threshold {CONSECUTIVE_THRESHOLD}). Latest: {reasons[0]}. The systemd "
           f"timer looks alive; the dispatcher itself is producing no spawns.")
    if dry_run:
        info["would_ntfy"] = True
        return True, info

    info["ntfy"] = notify_sinks.notify_critical(msg, title="foreman dispatcher NOT DISPATCHING")
    state["last_alarm_ts"] = now.isoformat(timespec="seconds")
    state["last_alarm_pass_id"] = most_recent_pass_id
    save_state(state)
    return True, info


# ---- CLI ------------------------------------------------------------------------------

def cmd_status(_args):
    streak, most_recent_pass_id, reasons = recent_unhealthy_streak()
    print(json.dumps({"streak": streak, "threshold": CONSECUTIVE_THRESHOLD,
                       "most_recent_pass_id": most_recent_pass_id, "reasons": reasons},
                     indent=2))
    return 0


def cmd_check(args):
    alarmed, info = check_dispatcher_health(dry_run=args.dry_run)
    print(json.dumps({"alarmed": alarmed, **info}, indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="foreman dispatcher-not-dispatching alarm (wq#825)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="print current streak, zero side effects")
    c = sub.add_parser("check", help="run one check: may ntfy + update dedup state")
    c.add_argument("--dry-run", action="store_true",
                   help="decide + report would_ntfy; zero side effects")
    a = ap.parse_args(argv)
    return {"status": cmd_status, "check": cmd_check}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
