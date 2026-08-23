#!/usr/bin/env python3
"""dispatch_config_lint.py — lane/config drift probe (wq#845).

PROBLEM (wq#760 lane autonomy audit, reports/opsaudit_a10_lane_defaults.md §4b): no probe
detects when a lane has open tickets in queue.db but no entry in the dispatcher's
config.json. The `build` lane was invisible to the dispatcher for an entire campaign
because it was never added to config.json — its tickets just piled up unclaimed.

CHECKS:
  FAIL — a lane has status=open tickets in queue.db but no entry in config.json["lanes"].
         The dispatcher will never see or dispatch these tickets.
  WARN — a lane has a config.json entry but zero tickets in any status in queue.db.
         Not necessarily wrong (a lane can be provisioned ahead of tickets showing up),
         but worth surfacing.

Any FAIL fires an ntfy alert (dedup 1h, same pattern as dispatch_health.py). WARN-only
runs print detail but do not alert.

CLI:
  dispatch_config_lint.py check [--dry-run]
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("FOREMAN_DISPATCHER_CONFIG",
                             os.path.join(DISPATCHER_DIR, "config.json"))
DB_PATH = os.environ.get("WQ_DB", "/home/lumen/agents/work-queue/queue.db")
STATE_PATH = os.environ.get("FOREMAN_CONFIG_LINT_STATE",
                            os.path.join(DISPATCHER_DIR, "config_lint_state.json"))
NTFY_BIN = os.environ.get("FOREMAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")

ALERT_DEDUP_SECONDS = 3600  # don't re-alert within 1h


def _utcnow():
    return datetime.now(timezone.utc)


def _ntfy(msg, priority="urgent", title="LANE/CONFIG DRIFT"):
    try:
        subprocess.run([NTFY_BIN, "-t", "lumen-alerts", "-p", priority, "-T", title, msg],
                       capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        pass


def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_PATH)


def load_config_lanes(config_path):
    with open(config_path) as f:
        cfg = json.load(f)
    return set(cfg.get("lanes", {}).keys())


def load_db_lane_counts(db_path):
    """Return (open_counts, all_counts) — lane -> count of open tickets, lane -> total count."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        open_counts = dict(conn.execute(
            "SELECT lane, COUNT(*) FROM tasks WHERE status='open' GROUP BY lane"))
        all_counts = dict(conn.execute(
            "SELECT lane, COUNT(*) FROM tasks GROUP BY lane"))
    finally:
        conn.close()
    return open_counts, all_counts


def lint(config_path=None, db_path=None):
    """Compare config.json lanes against queue.db lanes.

    Returns (fails, warns) — each a list of detail strings.
    """
    config_lanes = load_config_lanes(config_path or CONFIG_PATH)
    open_counts, all_counts = load_db_lane_counts(db_path or DB_PATH)

    fails = []
    for lane, n in sorted(open_counts.items()):
        if n > 0 and lane not in config_lanes:
            fails.append(f"lane={lane!r} has {n} open ticket(s) in queue.db but no "
                         f"config.json entry — dispatcher will never see them")

    warns = []
    for lane in sorted(config_lanes):
        if all_counts.get(lane, 0) == 0:
            warns.append(f"lane={lane!r} has a config.json entry but 0 tickets in queue.db")

    return fails, warns


def check_cmd(dry_run=False):
    fails, warns = lint()

    lines = []
    if fails:
        lines.append(f"FAIL ({len(fails)}):")
        lines.extend("  " + f for f in fails)
    if warns:
        lines.append(f"WARN ({len(warns)}):")
        lines.extend("  " + w for w in warns)
    if not fails and not warns:
        lines.append("OK — no lane/config drift detected")
    detail = "\n".join(lines)

    if not fails:
        return False, detail

    state = load_state()
    last_alert = state.get("last_alert_ts")
    now_iso = _utcnow().isoformat(timespec="seconds")
    if last_alert:
        try:
            elapsed = (_utcnow() - datetime.fromisoformat(last_alert)).total_seconds()
            if elapsed < ALERT_DEDUP_SECONDS:
                return False, detail + f"\n(suppressed — last alert {int(elapsed)}s ago)"
        except ValueError:
            pass

    if not dry_run:
        msg = f"{len(fails)} lane(s) with open tickets missing from dispatcher config:\n" + \
              "\n".join(fails)
        _ntfy(msg)
        save_state({"last_alert_ts": now_iso})

    return True, detail


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="check lane/config drift and alert on FAIL")
    c.add_argument("--dry-run", action="store_true", help="don't send ntfy or write state")

    args = p.parse_args(argv)

    if args.cmd == "check":
        fired, detail = check_cmd(dry_run=args.dry_run)
        print(detail)
        sys.exit(2 if fired else 0)


if __name__ == "__main__":
    main()
