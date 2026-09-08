#!/usr/bin/env python3
"""wq#1300: measures rework convergence from real fworker attempt records.

For each ticket under $FOREMAN_RUNS_ROOT/wq<id>/, reads attempt-N.json files
(fworker.py's per-attempt classification, see harness/fworker.py run_dir/
next_attempt_n) in order and finds how many attempts it took before the
classification first left FALSE_COMPLETE. That is the wq#17 signature named in
/home/lumen/kb/ISSUE-SOLUTIONS-fable.md #1 ("the done-then-critic-reverted count
... goes to zero, and the wq attempt histogram shows rework converging in <=2
attempts") — reported here as a checked number, not an assumption. No LLM calls;
this is pure filesystem analysis, safe to run anywhere including across the bridge.
"""
import argparse
import glob
import json
import os
import re
import statistics
import sys

DEFAULT_RUNS_ROOT = os.environ.get(
    "FOREMAN_RUNS_ROOT",
    os.path.join(os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman"), "_runs"),
)


def load_attempts(runs_root, ticket_dir):
    # wq#1499: attempt-N.uncharged.json shares the glob but is a harness fault, not an
    # attempt — skip it, and never let the sort key hit a name the anchored regex rejects.
    files = sorted(
        (p for p in glob.glob(os.path.join(runs_root, ticket_dir, "attempt-*.json"))
         if re.search(r"^attempt-(\d+)\.json$", os.path.basename(p))),
        key=lambda p: int(re.search(r"^attempt-(\d+)\.json$", os.path.basename(p)).group(1)),
    )
    out = []
    for f in files:
        try:
            with open(f) as fh:
                out.append(json.load(fh))
        except (OSError, ValueError):
            continue
    return out


def converged_at(attempts):
    """1-based attempt number where classification first left FALSE_COMPLETE, or
    None if every recorded attempt was FALSE_COMPLETE."""
    for a in attempts:
        if a.get("classification") != "FALSE_COMPLETE":
            return a.get("attempt")
    return None


def scan(runs_root):
    results = []
    if not os.path.isdir(runs_root):
        return results
    for entry in sorted(os.listdir(runs_root)):
        if not re.match(r"^wq\d+$", entry):
            continue
        attempts = load_attempts(runs_root, entry)
        if not any(a.get("classification") == "FALSE_COMPLETE" for a in attempts):
            continue
        results.append({
            "ticket": entry,
            "false_complete_attempts": sum(
                1 for a in attempts if a.get("classification") == "FALSE_COMPLETE"),
            "total_attempts_seen": len(attempts),
            "converged_at_attempt": converged_at(attempts),
        })
    return results


def summarize(results):
    converged = [r["converged_at_attempt"] for r in results if r["converged_at_attempt"] is not None]
    return {
        "tickets_with_false_complete": len(results),
        "converged_count": len(converged),
        "unconverged_count": len(results) - len(converged),
        "converged_within_2_attempts": sum(1 for c in converged if c <= 2),
        "median_convergence_attempt": statistics.median(converged) if converged else None,
        "convergence_rate_le2_pct": round(100.0 * sum(1 for c in converged if c <= 2) / len(results), 2)
        if results else None,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs-root", default=DEFAULT_RUNS_ROOT)
    args = ap.parse_args(argv)
    results = scan(args.runs_root)
    print(json.dumps({"summary": summarize(results), "tickets": results}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
