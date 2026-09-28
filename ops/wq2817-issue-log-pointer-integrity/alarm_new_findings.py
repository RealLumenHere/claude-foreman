#!/usr/bin/env python3
"""wq#2857: alarm when kb/ISSUE-LOG.md grows a pointer-integrity finding that
was not already in the pinned baseline.

probe_pointers.py (wq#2817) already reports every DUP/DANGLING/ONE-WAY finding
in the log, but the live file carries 128 of them today (wq#2817 baselined 124
at rev 10e77b2; two more collisions -- row 869 from wq#2787/wq#2836, row 885
from a run that landed while this ticket was in flight -- have been appended
since). Running probe_pointers.py alone as a periodic check would therefore
either fail on every run forever (useless as an alarm) or need re-baselining
by hand each time it's widened to a new backlog row. This script instead
diffs the live finding set against a pinned baseline snapshot
(known-findings-baseline.json) and alarms only on a finding that was not
already known -- exactly a colliding append, caught within one run of the
timer instead of surfacing two days later in a downstream ticket (wq#2763's
finding pattern).

Usage:
  alarm_new_findings.py                                   # live kb file vs committed baseline
  alarm_new_findings.py --path /path/to/ISSUE-LOG.md
  alarm_new_findings.py --baseline /path/to/baseline.json
  alarm_new_findings.py --rev 10e77b2                      # probe a pinned rev instead of live

Exit 0 when no new finding exists, 1 when at least one does (or the baseline
file is missing/unreadable). Never mutates the baseline -- rebaselining after
a deliberate cleanup or an accepted new backlog row is a separate, explicit
step (see README.md), not something a periodic run should do silently.
"""
import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BASELINE = os.path.join(HERE, "known-findings-baseline.json")

_spec = importlib.util.spec_from_file_location(
    "probe_pointers", os.path.join(HERE, "probe_pointers.py")
)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


def load_baseline(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return set(data["keys"]), data


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rev", help="read ISSUE-LOG.md from this kb rev instead of the live file")
    ap.add_argument("--path", default=f"{probe.KB_DIR}/ISSUE-LOG.md")
    ap.add_argument("--baseline", default=DEFAULT_BASELINE)
    args = ap.parse_args()

    try:
        baseline_keys, meta = load_baseline(args.baseline)
    except (OSError, json.JSONDecodeError, KeyError) as e:
        print(f"alarm_new_findings: cannot read baseline {args.baseline}: {e}", file=sys.stderr)
        return 1

    found = probe.rows(probe.load(args.rev, args.path))
    key_to_text = dict(probe._findings(found))
    live_keys = set(key_to_text)

    new_keys = sorted(live_keys - baseline_keys)
    resolved_keys = sorted(baseline_keys - live_keys)

    source = f"rev {args.rev}" if args.rev else args.path
    print(
        f"source: {source}  baseline: {args.baseline} (pinned {meta.get('captured', '?')}, "
        f"{len(baseline_keys)} known)  live findings: {len(live_keys)}"
    )
    for key in resolved_keys:
        print(f"RESOLVED (no longer present, baseline unchanged): {key}")
    for key in new_keys:
        print(f"NEW {key_to_text[key]}")

    if new_keys:
        print(f"ALARM ({len(new_keys)} new finding(s) not in baseline)")
        return 1
    print("OK (no new findings beyond baseline)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
