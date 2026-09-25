#!/usr/bin/env python3
"""cron_deadman.py — Healthchecks.io-style dead-man's switch for lumen-*
cron/timer stamp files (wq#1184).

Any lumen-* systemd service that wants coverage touches
/home/lumen/logs/.stamp.<unit-name> on success (see cron_stamp.sh — wire it
in with `ExecStartPost=-/home/lumen/bin/lumen-cron-stamp %n`). This script
globs those stamp files, looks up each matching unit's *actual* trigger
cadence from `systemctl list-timers` (next-elapse minus last-trigger, which
works for both OnCalendar and OnUnitActiveSec timers without parsing either
syntax), and alerts via ntfy-send if a stamp is older than 2x that interval.

Coverage is opt-in and grows as more lumen-* units adopt the stamp wrapper —
this only ever checks stamps that exist, it does not require every lumen-*
unit to participate (see RUNBOOK.md).

Usage:
  cron_deadman.py check [--stamp-dir DIR] [--state PATH] [--dry-run]
"""
import argparse
import json
import os
import subprocess
import sys
import time

STAMP_DIR = "/home/lumen/logs"
STAMP_PREFIX = ".stamp."
RUN_STATE_PREFIX = ".state."
STATE_PATH = "/home/lumen/foreman/ops/wq1184-gatus-health-checks/deadman_state.json"
NTFY_BIN = "/home/lumen/bin/ntfy-send"
UNARMED_GRACE_S = 3600


def _row_unit(row):
    activates = row.get("activates", "")
    name = activates[:-len(".service")] if activates.endswith(".service") else activates
    return name or row.get("unit", "")[:-len(".timer")]


def load_timer_intervals():
    """(intervals, unarmed). intervals: unit-name (no .service/.timer suffix)
    -> interval in seconds, derived from next-elapse minus last-trigger; timers
    that haven't fired yet (last == 0) are skipped — nothing to compare a stamp
    against. unarmed: units whose timer is loaded but has no next elapse, i.e.
    disabled or stopped (wq#2107: lumen-public-guardian.timer sat disabled for a
    week because a timer with no next elapse produced no cadence and no alert)."""
    try:
        out = subprocess.run(
            ["systemctl", "list-timers", "--all", "-o", "json"],
            capture_output=True, text=True, timeout=20, check=True,
        ).stdout
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        print(f"cron_deadman: systemctl list-timers failed: {exc}", file=sys.stderr)
        return {}, set()

    intervals, unarmed = {}, set()
    for row in json.loads(out):
        last = row.get("last") or 0
        next_ = row.get("next") or 0
        if not next_:
            unarmed.add(_row_unit(row))
        elif last and next_ > last:
            intervals[_row_unit(row)] = (next_ - last) / 1_000_000
    return intervals, unarmed


def find_stamps(stamp_dir):
    if not os.path.isdir(stamp_dir):
        return {}
    stamps = {}
    for fname in os.listdir(stamp_dir):
        if fname.startswith(STAMP_PREFIX):
            unit = fname[len(STAMP_PREFIX):]
            path = os.path.join(stamp_dir, fname)
            stamps[unit] = os.stat(path).st_mtime
    return stamps


def find_run_states(stamp_dir):
    """wq#2573: units may additionally drop /home/lumen/logs/.state.<unit> —
    a JSON {"status": "ok"|"error", "detail": ...} written by the job itself
    on EVERY exit path (success or failure). This exists because a
    Type=oneshot service's ExecStartPost= (the source of the plain .stamp.
    file) never runs when ExecStart exits non-zero, so an in-script
    fail-loud exit is invisible to the age-based staleness check below for
    up to 2x the unit's cadence. Only consulted for units that already have
    an ordinary stamp (same opt-in coverage model)."""
    if not os.path.isdir(stamp_dir):
        return {}
    states = {}
    for fname in os.listdir(stamp_dir):
        if not fname.startswith(RUN_STATE_PREFIX):
            continue
        unit = fname[len(RUN_STATE_PREFIX):]
        try:
            with open(os.path.join(stamp_dir, fname)) as f:
                states[unit] = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
    return states


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def ntfy(priority, title, msg, dry_run):
    if dry_run:
        print(f"[dry-run] ntfy-send -p {priority} -T {title!r} {msg!r}")
        return
    try:
        subprocess.run([NTFY_BIN, "-p", priority, "-T", title, msg],
                        capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"cron_deadman: ntfy-send failed: {exc}", file=sys.stderr)


def check(stamp_dir, state_path, dry_run):
    intervals, unarmed = load_timer_intervals()
    stamps = find_stamps(stamp_dir)
    run_states = find_run_states(stamp_dir)
    state = load_state(state_path)
    now = time.time()
    changed = False

    for unit, mtime in stamps.items():
        interval_s = intervals.get(unit)
        if interval_s is None and unit in unarmed:
            # No cadence to derive, but the timer is loaded and not scheduled:
            # a stamp this old on a disarmed timer is a silently dead job.
            threshold, why = UNARMED_GRACE_S, "timer is not armed (disabled/stopped)"
        elif interval_s is None:
            # Stamp exists but no matching timer found (renamed/removed unit,
            # or timer hasn't fired twice yet to establish a cadence) — skip,
            # don't guess at a threshold.
            continue
        else:
            threshold = 2 * interval_s
            why = f"2x its ~{interval_s:.0f}s cadence"
        age = now - mtime
        was_stale = state.get(unit) == "stale"
        is_stale = age > threshold

        if is_stale and not was_stale:
            ntfy("urgent", f"Dead-man: {unit} stale",
                 f"{unit} stamp is {age:.0f}s old (threshold {threshold:.0f}s, "
                 f"{why}) — wq#1184",
                 dry_run)
            changed = True
        elif was_stale and not is_stale:
            ntfy("high", f"Dead-man: {unit} recovered",
                 f"{unit} stamp is fresh again ({age:.0f}s old) — wq#1184",
                 dry_run)
            changed = True

        state[unit] = "stale" if is_stale else "ok"

        rs = run_states.get(unit)
        if rs is not None:
            run_key = f"{unit}:run"
            is_error = rs.get("status") == "error"
            was_error = state.get(run_key) == "error"
            if is_error and not was_error:
                ntfy("urgent", f"Dead-man: {unit} reported failure",
                     f"{unit}'s last run reported status=error: "
                     f"{str(rs.get('detail', ''))[:300]} — wq#2573",
                     dry_run)
                changed = True
            elif was_error and not is_error:
                ntfy("high", f"Dead-man: {unit} run recovered",
                     f"{unit}'s last run reported status=ok — wq#2573", dry_run)
                changed = True
            state[run_key] = "error" if is_error else "ok"

    # Drop state for stamps that no longer exist (unit retired / stamp wrapper removed).
    for key in list(state):
        base = key[:-len(":run")] if key.endswith(":run") else key
        if base not in stamps:
            del state[key]
            changed = True

    if changed and not dry_run:
        save_state(state_path, state)

    return state


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="check stamp freshness and alert on transitions")
    c.add_argument("--stamp-dir", default=STAMP_DIR)
    c.add_argument("--state", default=STATE_PATH)
    c.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.cmd == "check":
        state = check(args.stamp_dir, args.state, args.dry_run)
        stale = [u for u, s in state.items() if s == "stale"]
        print(f"cron_deadman: {len(state)} stamp(s) tracked, {len(stale)} stale: {stale}")


if __name__ == "__main__":
    main()
