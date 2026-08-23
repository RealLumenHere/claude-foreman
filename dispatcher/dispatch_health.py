#!/usr/bin/env python3
"""dispatch_health.py — dispatcher dead-man's switch (wq#825).

PROBLEM (2026-08-22): the dispatcher looped 600+ times over 10h with every pass dying
at stage=config. Night-watch and self_heal.py watch WORKER verdicts, not DISPATCHER
health, so nobody paged Frank.

FIX: this script reads the dispatcher log, classifies the last N passes, and fires an
URGENT ntfy if all N are unhealthy. Runs on its own systemd timer alongside the
dispatcher.

A pass is UNHEALTHY if:
  - It emitted `error stage=config` and has NO `pass_end` (hard abort).
  - Its `pass_end` has spawned=0 AND candidates>0 AND dry_run=false AND no
    `halt stage=concurrency reason=no_slots` in the same pass (= the dispatcher COULD
    have spawned but didn't, and it wasn't because all slots were full).

A pass is HEALTHY if:
  - It has a `pass_end` with spawned>0, OR
  - It has a `pass_end` with candidates=0 (nothing to do), OR
  - It halted at concurrency/no_slots (ceiling reached, working as designed), OR
  - It was dry_run=true (config_lane soft error, still made decisions).

CLI:
  dispatch_health.py check [--threshold N] [--log PATH] [--dry-run]
  dispatch_health.py inject-fault    # write a bad config for testing
  dispatch_health.py clear-fault     # restore good config
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime, timezone

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.environ.get("FOREMAN_DISPATCH_LOG",
                          os.path.join(os.path.dirname(DISPATCHER_DIR), "dispatcher.log"))
STATE_PATH = os.environ.get("FOREMAN_HEALTH_STATE",
                            os.path.join(DISPATCHER_DIR, "health_state.json"))
CONFIG_PATH = os.path.join(DISPATCHER_DIR, "config.json")
NTFY_BIN = os.environ.get("FOREMAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")

DEFAULT_THRESHOLD = 3          # consecutive unhealthy passes before alarm
ALERT_DEDUP_SECONDS = 3600     # don't re-alert within 1h
# How many log lines to scan from the tail. 200 lines covers ~30 passes at ~6 lines each.
TAIL_LINES = 200


def _utcnow():
    return datetime.now(timezone.utc)


def _ntfy(msg, priority="urgent", title="DISPATCHER STALLED"):
    try:
        subprocess.run([NTFY_BIN, "-t", "lumen-alerts", "-p", priority, "-T", title, msg],
                       capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        pass


def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_PATH)


def parse_log_tail(log_path, n_lines):
    """Read last n_lines of the dispatcher log and return parsed JSON events."""
    events = []
    try:
        with open(log_path) as f:
            lines = f.readlines()
        for line in lines[-n_lines:]:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue
    except OSError:
        pass
    return events


def classify_passes(events):
    """Group events by pass_id, classify each pass as healthy/unhealthy.

    Returns list of (pass_id, ts, verdict, reason) in chronological order,
    where verdict is 'healthy', 'unhealthy', or 'skip'.
    """
    passes = OrderedDict()
    for ev in events:
        pid = ev.get("pass_id", "")
        if not pid or not pid.startswith("fdisp-"):
            continue
        passes.setdefault(pid, []).append(ev)

    results = []
    for pid, evs in passes.items():
        ts = evs[0].get("ts", "")

        has_pass_end = False
        has_config_error = False
        is_dry_run = False
        spawned = 0
        candidates = 0
        has_no_slots_halt = False

        for ev in evs:
            event = ev.get("event", "")
            if event == "error" and ev.get("stage") == "config":
                has_config_error = True
            if event == "pass_start":
                if ev.get("dry_run"):
                    is_dry_run = True
            if event == "pass_end":
                has_pass_end = True
                spawned = ev.get("spawned", 0)
                candidates = ev.get("candidates", 0)
            if event == "halt" and ev.get("stage") == "concurrency" \
               and "no_slots" in (ev.get("reason") or ""):
                has_no_slots_halt = True

        # Skip dry-run-only passes (config_lane soft errors; the dispatcher still made
        # decisions, just didn't execute them — that's degraded, not dead).
        if is_dry_run and not has_config_error:
            results.append((pid, ts, "skip", "dry_run pass"))
            continue

        # Case 1: hard config error, no pass_end → dispatcher dead this cycle.
        if has_config_error and not has_pass_end:
            results.append((pid, ts, "unhealthy", "config error (pass aborted)"))
            continue

        # Case 2: pass completed but spawned nothing despite candidates waiting,
        # and it wasn't because all slots were full.
        if has_pass_end and spawned == 0 and candidates > 0 \
           and not has_no_slots_halt and not is_dry_run:
            results.append((pid, ts, "unhealthy",
                            f"spawned=0 candidates={candidates} (not concurrency-limited)"))
            continue

        # Everything else is healthy (or at least not the dispatcher's fault).
        if has_pass_end:
            results.append((pid, ts, "healthy",
                            f"spawned={spawned} candidates={candidates}"))
        elif has_config_error:
            # config error but somehow got a pass_end → shouldn't happen, but be safe
            results.append((pid, ts, "unhealthy", "config error with unexpected pass_end"))
        else:
            # No pass_end, no config error — possibly incomplete log line, skip
            results.append((pid, ts, "skip", "incomplete pass data"))

    return results


def check_health(log_path, threshold, dry_run=False):
    """Main check. Returns (alarm_fired, detail_string)."""
    events = parse_log_tail(log_path, TAIL_LINES)
    if not events:
        return False, "no log data"

    results = classify_passes(events)
    # Filter to only scored passes (healthy/unhealthy), skip dry-run/incomplete.
    scored = [(pid, ts, v, r) for pid, ts, v, r in results if v != "skip"]
    if not scored:
        return False, "no scorable passes in log tail"

    # Take the last `threshold` scored passes.
    recent = scored[-threshold:]
    all_unhealthy = all(v == "unhealthy" for _, _, v, _ in recent)

    detail_lines = []
    for pid, ts, v, r in recent:
        detail_lines.append(f"  {ts} {v}: {r}")

    if not all_unhealthy:
        return False, f"OK — last {len(recent)} passes:\n" + "\n".join(detail_lines)

    # All recent passes are unhealthy — check dedup.
    state = load_state()
    last_alert = state.get("last_alert_ts")
    now_iso = _utcnow().isoformat(timespec="seconds")
    if last_alert:
        try:
            elapsed = (_utcnow() - datetime.fromisoformat(last_alert)).total_seconds()
            if elapsed < ALERT_DEDUP_SECONDS:
                return False, (f"UNHEALTHY but suppressed (last alert {int(elapsed)}s ago, "
                               f"dedup={ALERT_DEDUP_SECONDS}s):\n" + "\n".join(detail_lines))
        except ValueError:
            pass

    # Fire.
    reasons = "; ".join(r for _, _, _, r in recent)
    msg = (f"Dispatcher STALLED: last {threshold} passes all unhealthy. "
           f"Backlog not moving.\n{reasons}")

    if dry_run:
        print(f"WOULD ALERT: {msg}")
        return True, f"ALARM (dry-run):\n" + "\n".join(detail_lines)

    _ntfy(msg)
    state["last_alert_ts"] = now_iso
    save_state(state)
    return True, f"ALARM FIRED:\n" + "\n".join(detail_lines)


# ---- fault injection for testing (wq#825 acceptance test) ----------------------------

FAULT_KEY = "__HEALTH_TEST_FAULT__"
FAULT_BAK = CONFIG_PATH + ".bak.healthtest"


def inject_fault():
    """Write a structurally invalid config that will make the dispatcher emit
    error stage=config on its next pass. Backs up the real config first.

    NOTE (wq#827 verify fix, 2026-08-23): the original version added a bad lane
    (__HEALTHTEST__ with max_per_day:-999), but wq#824 made per-lane validation
    SOFT (stage=config_lane, not stage=config), so the health checker correctly
    classified the pass as healthy (it continued and spawned). This version
    corrupts a top-level required field (max_spawns_per_pass) instead, which is
    a hard config error → the pass aborts → dispatch_health.py detects it."""
    with open(CONFIG_PATH) as f:
        real = f.read()
    with open(FAULT_BAK, "w") as f:
        f.write(real)
    cfg = json.loads(real)
    cfg["max_spawns_per_pass"] = "HEALTHTEST_INVALID"
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"FAULT INJECTED: set max_spawns_per_pass to invalid value in {CONFIG_PATH}")
    print(f"Backup: {FAULT_BAK}")
    print("The dispatcher's next pass will emit error stage=config (hard abort).")


def clear_fault():
    """Restore the real config from the fault-injection backup."""
    if not os.path.exists(FAULT_BAK):
        print(f"No fault backup at {FAULT_BAK} — nothing to restore.")
        return
    with open(FAULT_BAK) as f:
        real = f.read()
    with open(CONFIG_PATH, "w") as f:
        f.write(real)
    os.remove(FAULT_BAK)
    print(f"Config restored from {FAULT_BAK}.")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Dispatcher dead-man's switch (wq#825)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="check dispatcher health and alert if stalled")
    c.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                   help=f"consecutive unhealthy passes before alarm (default {DEFAULT_THRESHOLD})")
    c.add_argument("--log", default=LOG_PATH, help="dispatcher log path")
    c.add_argument("--dry-run", action="store_true",
                   help="classify + report, don't ntfy")

    sub.add_parser("inject-fault", help="write a bad config for acceptance testing")
    sub.add_parser("clear-fault", help="restore config after testing")

    # For unit testing: parse a log snippet and print classifications.
    d = sub.add_parser("classify", help="parse log and print pass classifications")
    d.add_argument("--log", default=LOG_PATH)

    a = ap.parse_args(argv)

    if a.cmd == "check":
        fired, detail = check_health(a.log, a.threshold, dry_run=a.dry_run)
        print(detail)
        sys.exit(2 if fired else 0)

    elif a.cmd == "inject-fault":
        inject_fault()

    elif a.cmd == "clear-fault":
        clear_fault()

    elif a.cmd == "classify":
        events = parse_log_tail(a.log, TAIL_LINES)
        results = classify_passes(events)
        for pid, ts, v, r in results:
            print(f"{ts} [{v:>9}] {r}")


if __name__ == "__main__":
    main()
