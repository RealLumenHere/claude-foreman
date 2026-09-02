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
STATE_PATH = "/home/lumen/foreman/ops/wq1184-gatus-health-checks/deadman_state.json"
NTFY_BIN = "/home/lumen/bin/ntfy-send"


def load_timer_intervals():
    """unit-name (no .service/.timer suffix) -> interval in seconds, derived
    from next-elapse minus last-trigger. Skips timers that haven't fired yet
    (last == 0) — nothing to compare a stamp against."""
    try:
        out = subprocess.run(
            ["systemctl", "list-timers", "--all", "-o", "json"],
            capture_output=True, text=True, timeout=20, check=True,
        ).stdout
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        print(f"cron_deadman: systemctl list-timers failed: {exc}", file=sys.stderr)
        return {}

    intervals = {}
    for row in json.loads(out):
        last = row.get("last") or 0
        next_ = row.get("next") or 0
        if not last or not next_ or next_ <= last:
            continue
        interval_s = (next_ - last) / 1_000_000
        activates = row.get("activates", "")
        name = activates[:-len(".service")] if activates.endswith(".service") else activates
        if not name:
            name = row.get("unit", "")[:-len(".timer")]
        intervals[name] = interval_s
    return intervals


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
    intervals = load_timer_intervals()
    stamps = find_stamps(stamp_dir)
    state = load_state(state_path)
    now = time.time()
    changed = False

    for unit, mtime in stamps.items():
        interval_s = intervals.get(unit)
        if interval_s is None:
            # Stamp exists but no matching timer found (renamed/removed unit,
            # or timer hasn't fired twice yet to establish a cadence) — skip,
            # don't guess at a threshold.
            continue
        threshold = 2 * interval_s
        age = now - mtime
        was_stale = state.get(unit) == "stale"
        is_stale = age > threshold

        if is_stale and not was_stale:
            ntfy("urgent", f"Dead-man: {unit} stale",
                 f"{unit} stamp is {age:.0f}s old (threshold {threshold:.0f}s, "
                 f"2x its ~{interval_s:.0f}s cadence) — wq#1184",
                 dry_run)
            changed = True
        elif was_stale and not is_stale:
            ntfy("high", f"Dead-man: {unit} recovered",
                 f"{unit} stamp is fresh again ({age:.0f}s old) — wq#1184",
                 dry_run)
            changed = True

        state[unit] = "stale" if is_stale else "ok"

    # Drop state for stamps that no longer exist (unit retired / stamp wrapper removed).
    for unit in list(state):
        if unit not in stamps:
            del state[unit]
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
