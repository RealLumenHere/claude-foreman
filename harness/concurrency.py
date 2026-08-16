#!/usr/bin/env python3
"""concurrency — cloud-worker concurrency cap. BUILD_PLAN.md §T2b (wq#616).

FOREMAN_RESEARCH.md §8: "Concurrency cap: start 2, ceiling 3 cloud workers + N
local-rung workers (local unlimited by Anthropic, bounded by Lumen CPU)." This module
tracks the cloud half — local rung (T4) isn't built yet and isn't counted here.

Counts LIVE `fworker-wq*` lumen-run units, not wq claims: a claim can outlive a dead
supervisor process (that staleness is reaper.py's job, wq#647, on its own sweep
schedule) — this module answers a different question, "how many `claude -p` processes
are actually running against the shared login right now", which only a live process
listing can answer.

Usage:
  concurrency.py running [--prefix fworker-wq]      # JSON {count, units}
  concurrency.py check   [--prefix P] [--ceiling N] # exit 0 room / 1 at-or-over ceiling
"""
import argparse
import json
import os
import re
import subprocess
import sys

LUMEN_RUN_BIN = os.environ.get("FOREMAN_LUMEN_RUN_BIN", "/home/lumen/bin/lumen-run")

# FOREMAN_RESEARCH.md §8. CLOUD_START is the batch-size default spawner.py dispatches
# at once; CLOUD_CEILING is the hard cap this module enforces.
CLOUD_START = 2
CLOUD_CEILING = 3

# `lumen-run list` unit column, e.g. "lumen-job-fworker-wq616-1786850106.service".
# Non-greedy name capture so a numeric ticket-id slug (fworker-wq616) doesn't get
# split at its own digits — see harness/tests/test_concurrency.py for the boundary
# case this depends on.
UNIT_RE = re.compile(r"lumen-job-(?P<name>\S+?)-\d+\.service")


class LumenRunError(RuntimeError):
    pass


def list_running_units(prefix="fworker-wq"):
    """Live `fworker-wq*` unit names currently RUNNING per `lumen-run list`. Raises
    LumenRunError on any failure to reach lumen-run — a caller should treat that as
    "assume the cap is full", never as "assume zero are running"."""
    try:
        p = subprocess.run([LUMEN_RUN_BIN, "list"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise LumenRunError(f"lumen-run list failed: {e}") from e
    if p.returncode != 0:
        raise LumenRunError(f"lumen-run list exited {p.returncode}: {p.stderr.strip()}")
    units = []
    for line in p.stdout.splitlines():
        if "running" not in line:
            continue
        m = UNIT_RE.search(line)
        if m and m.group("name").startswith(prefix):
            units.append(m.group("name"))
    return units


def slots_available(prefix="fworker-wq", ceiling=CLOUD_CEILING):
    """(available_slots, running_units). available_slots is clamped to >= 0."""
    units = list_running_units(prefix)
    return max(0, ceiling - len(units)), units


def cmd_running(args):
    units = list_running_units(args.prefix)
    print(json.dumps({"count": len(units), "units": units}))
    return 0


def cmd_check(args):
    avail, units = slots_available(args.prefix, args.ceiling)
    print(json.dumps({"available": avail, "running": len(units),
                       "ceiling": args.ceiling, "units": units}))
    return 0 if avail > 0 else 1


def main():
    ap = argparse.ArgumentParser(description="concurrency — cloud worker slot accounting")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("running", help="list currently-running fworker-wq* units")
    p_run.add_argument("--prefix", default="fworker-wq")
    p_run.set_defaults(func=cmd_running)

    p_chk = sub.add_parser("check", help="exit 0 if a slot is free, else 1")
    p_chk.add_argument("--prefix", default="fworker-wq")
    p_chk.add_argument("--ceiling", type=int, default=CLOUD_CEILING)
    p_chk.set_defaults(func=cmd_check)

    args = ap.parse_args()
    try:
        sys.exit(args.func(args))
    except LumenRunError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
