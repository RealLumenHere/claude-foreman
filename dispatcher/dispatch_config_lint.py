#!/usr/bin/env python3
"""dispatch_config_lint.py — lane/config drift probe (wq#845).

PROBLEM (wq#760 lane autonomy audit, reports/opsaudit_a10_lane_defaults.md §4b): no probe
detects when a lane has open tickets in queue.db but no entry in the dispatcher's
config.json. The `build` lane was invisible to the dispatcher for an entire campaign
because it was never added to config.json — its tickets just piled up unclaimed.

CHECKS:
  FAIL — a lane has status=open tickets in queue.db but no COMMITTED entry in
         config.json["lanes"]. The dispatcher will never see or dispatch these tickets.
  WARN — a lane has a config.json entry but zero tickets in any status in queue.db.
         Not necessarily wrong (a lane can be provisioned ahead of tickets showing up),
         but worth surfacing.

Any FAIL fires an ntfy alert (dedup 1h, same pattern as dispatch_health.py). WARN-only
runs print detail but do not alert.

wq#1282 (#23): "COMMITTED" is load-bearing. foreman_dispatcher.load_config() reads
config.json at HEAD (wq#772), so a lane sitting only in the working-tree copy is not
dispatch-effective. This lint used to read the working tree, which made it blind to
exactly that drift — it reported OK while the dispatcher ignored the lane's tickets.

CLI:
  dispatch_config_lint.py check [--dry-run]

Exit: 0 clean/suppressed, 2 alert fired, 3 the lint itself could not run.
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DISPATCHER_DIR)
import vlogs_mirror  # noqa: E402

CONFIG_PATH = os.environ.get("FOREMAN_DISPATCHER_CONFIG",
                             os.path.join(DISPATCHER_DIR, "config.json"))
DB_PATH = os.environ.get("WQ_DB", "/home/lumen/agents/work-queue/queue.db")
STATE_PATH = os.environ.get("FOREMAN_CONFIG_LINT_STATE",
                            os.path.join(DISPATCHER_DIR, "runtime", "config_lint_state.json"))
# wq#1253 (#23): moved under runtime/ (gitignored); one-release fallback below for a
# pre-migration state file still at the old tracked location.
_LEGACY_STATE_PATH = os.path.join(DISPATCHER_DIR, "config_lint_state.json")
# wq#1282 (#23): wq-lane-init stages auto-registered lanes here instead of editing the
# tracked config.json. Gitignored, so a new lane no longer dirties the primary tree.
LANE_STATE_PATH = os.environ.get("FOREMAN_LANE_STATE",
                                 os.path.join(DISPATCHER_DIR, "runtime", "lane_state.json"))
NTFY_BIN = os.environ.get("FOREMAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")

ALERT_DEDUP_SECONDS = 3600  # don't re-alert within 1h


def _utcnow():
    return datetime.now(timezone.utc)


def _ntfy(msg, priority="urgent", title="LANE/CONFIG DRIFT"):
    vlogs_mirror.mirror("dispatch_config_lint", title, priority, msg)
    try:
        subprocess.run([NTFY_BIN, "-t", "lumen-alerts", "-p", priority, "-T", title, msg],
                       capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        pass


def load_state():
    path = STATE_PATH if os.path.exists(STATE_PATH) else _LEGACY_STATE_PATH
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    d = os.path.dirname(STATE_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_PATH)


class LintError(Exception):
    """The lint could not be performed (not the same thing as drift being found)."""


def _git_show_committed(path):
    """Return `path` as it stands COMMITTED at HEAD of its containing repo.

    Mirrors foreman_dispatcher._git_show_committed (wq#772) on purpose: the lint has to
    read config the way the dispatcher reads it, or it lints a file nobody dispatches
    from."""
    abspath = os.path.abspath(path)
    repo_dir = os.path.dirname(abspath)

    def git(*args):
        try:
            p = subprocess.run(["git", "-C", repo_dir, *args],
                               capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise LintError(f"config {path}: git {' '.join(args)} failed: {e}")
        if p.returncode != 0:
            raise LintError(f"config {path}: git {' '.join(args)} failed: {p.stderr.strip()}")
        return p.stdout

    toplevel = git("rev-parse", "--show-toplevel").strip()
    relpath = os.path.relpath(abspath, toplevel)
    return git("show", f"HEAD:{relpath}")


def load_config_lanes(config_path):
    """Lanes the dispatcher will actually act on — config.json at HEAD, never the
    working tree (wq#1282)."""
    try:
        cfg = json.loads(_git_show_committed(config_path))
    except ValueError as e:
        raise LintError(f"config {config_path}: invalid JSON in committed config: {e}")
    return set(cfg.get("lanes", {}).keys())


def load_staged_lanes(lane_state_path=None):
    """Lanes wq-lane-init has staged in runtime/lane_state.json (wq#1282, #23).

    Staged is not live: the entry exists so the lane is visible and its scaffolding is
    reproducible, but it takes an explicit commit into config.json to become policy.
    Absent or malformed file means "nothing staged" — this is a reporting refinement,
    never a reason to fail the lint."""
    try:
        with open(lane_state_path or LANE_STATE_PATH) as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return set()
    lanes = state.get("lanes")
    return set(lanes) if isinstance(lanes, dict) else set()


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


def lint(config_path=None, db_path=None, lane_state_path=None):
    """Compare committed config.json lanes against queue.db lanes.

    Returns (fails, warns) — each a list of detail strings.
    """
    config_lanes = load_config_lanes(config_path or CONFIG_PATH)
    staged_lanes = load_staged_lanes(lane_state_path)
    open_counts, all_counts = load_db_lane_counts(db_path or DB_PATH)

    fails = []
    for lane, n in sorted(open_counts.items()):
        if n > 0 and lane not in config_lanes:
            if lane in staged_lanes:
                fails.append(f"lane={lane!r} has {n} open ticket(s) and a staged "
                             f"runtime/lane_state.json entry, but no COMMITTED "
                             f"config.json entry — the dispatcher reads config.json at "
                             f"HEAD, so it will never see them; commit the lane into "
                             f"config.json to make it live")
            else:
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
        try:
            fired, detail = check_cmd(dry_run=args.dry_run)
        except LintError as e:
            # Reading committed config failed (wq#1282). Exit non-zero so the timer's
            # journal shows it, but don't ntfy — a git hiccup is not lane drift.
            print(f"ERROR — lint could not run: {e}")
            sys.exit(3)
        print(detail)
        sys.exit(2 if fired else 0)


if __name__ == "__main__":
    main()
