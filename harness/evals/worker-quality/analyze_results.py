#!/usr/bin/env python3
"""wq#1300: turns a promptfoo results.json into the two numbers the ticket asks for.

false_complete_rate: of the cases labeled expected_verdict=rework (a done-claim that
should have been rejected), the fraction the judge WRONGLY accepted as done. This is
the false-complete escape rate — the number wq#1289's evidence gate exists to drive to
zero (ISSUE-SOLUTIONS-fable.md #1: "the done-then-critic-reverted count ... goes to
zero").

accuracy: overall fraction of cases the judge scored correctly (both directions).
"""
import argparse
import json
import sys


def load_grades(results):
    """promptfoo's results.json shape has drifted across versions; this pulls
    (expected_verdict, pass) pairs from whichever of the two known shapes is present."""
    out = []
    body = results.get("results", results)
    rows = body.get("results") if isinstance(body, dict) else body
    if not isinstance(rows, list):
        return out
    for row in rows:
        v = (row.get("vars") or row.get("testCase", {}).get("vars") or {})
        expected = v.get("expected_verdict")
        grade = row.get("gradingResult") or {}
        passed = grade.get("pass")
        if passed is None:
            passed = bool(row.get("success"))
        if expected is not None:
            out.append((expected, bool(passed)))
    return out


def summarize(grades):
    n = len(grades)
    correct = sum(1 for _, ok in grades if ok)
    rework_cases = [ok for exp, ok in grades if exp == "rework"]
    done_cases = [ok for exp, ok in grades if exp == "done"]
    false_complete = len(rework_cases) - sum(rework_cases)  # rework case the judge said "done"
    return {
        "n_cases": n,
        "accuracy_pct": round(100.0 * correct / n, 2) if n else None,
        "n_rework_cases": len(rework_cases),
        "false_complete_count": false_complete,
        "false_complete_rate_pct": round(100.0 * false_complete / len(rework_cases), 2)
        if rework_cases else None,
        "n_done_cases": len(done_cases),
        "false_reject_count": len(done_cases) - sum(done_cases),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results_json", help="path to promptfoo's outputPath (tests/results.json)")
    args = ap.parse_args(argv)
    with open(args.results_json) as f:
        results = json.load(f)
    grades = load_grades(results)
    summary = summarize(grades)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
