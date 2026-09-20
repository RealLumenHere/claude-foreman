#!/usr/bin/env python3
"""read_dir_sweep_messenger.py — wq#1483 follow-up.

Standing daily masked sweep of /home/lumen/messenger/logs (where a Slack xapp- app
token was found pasted into chat history 2026-09-08, BROKER_GRANT_COVERAGE.md §3).

Deliberately NOT added to fleet-exec-policy.yaml allow.read_dirs: that list is a worker
READ-GRANT allowlist, and messenger's chat logs are exactly the kind of directory that
must stay OFF it (per the sweep's own §3 evidence table, "on allow? NO"). This is a
separate, ungranted, alert-only watch using the same sweep_root()/aq_alert() machinery
against its own runtime file, so a real leak here pages Frank without ever becoming a
worker read grant.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from read_dir_sweep import write_runtime, aq_alert  # noqa: E402

ROOT = "/home/lumen/messenger/logs"
RUNTIME_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "runtime", "read_dir_sweep_messenger.json")


def main():
    report, new_hits = write_runtime(roots=[ROOT], runtime_path=RUNTIME_PATH)
    aq_alert(new_hits)
    total = sum(len(r.get("hits") or []) for r in report.values())
    print(f"read_dir_sweep_messenger: wrote {RUNTIME_PATH} — {total} hits, {len(new_hits)} new")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
