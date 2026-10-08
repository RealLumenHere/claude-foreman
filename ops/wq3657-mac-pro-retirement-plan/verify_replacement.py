#!/usr/bin/env python3
"""wq#3657 phase-2 gate: did the M70s+BC-250 replacement actually beat the Mac Pro it
replaces, on decode tok/s and watts, for a given macbench row? Reads the same results
JSONL macbench already writes (tools/macbench/mb_measure.py) - no new measurement
path, just a verdict over existing data.

Exit 0 + PASS only if the candidate's decode tok/s meets --min-decode-ratio of the
baseline's AND its mean watts is at most --max-watts-ratio of the baseline's. Either
side can fail independently, and does fail on real inputs (see
ops/wq3657-mac-pro-retirement-plan/tests/test_verify_replacement.py) - a replacement
that is fast but still draws Mac-Pro-class power is a FAIL, not a pass with a caveat,
because the whole point of this consolidation (ticket wq#3657) is the watts line.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "macbench"))
import mb_render  # noqa: E402


def row_metrics(jsonl_path, row_id):
    records = mb_render.load(jsonl_path)
    rows = mb_render.collect(records)
    phases = rows.get(row_id)
    if not phases:
        raise SystemExit(f"row {row_id!r} not found in {jsonl_path}")
    decode_tps = mb_render._cell(phases, "decode", "decode_tps")
    watts = [r.get("watts_mean") for r in phases.values() if r.get("watts_mean") is not None]
    watts_mean = max(watts) if watts else None
    if decode_tps is None or watts_mean is None:
        raise SystemExit(f"row {row_id!r} in {jsonl_path} is missing decode_tps or watts_mean")
    return decode_tps, watts_mean


def verdict(baseline_tps, baseline_watts, candidate_tps, candidate_watts,
            min_decode_ratio, max_watts_ratio):
    decode_ok = candidate_tps >= baseline_tps * min_decode_ratio
    watts_ok = candidate_watts <= baseline_watts * max_watts_ratio
    return decode_ok, watts_ok


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", required=True, help="macbench results JSONL for the Mac Pro")
    ap.add_argument("--candidate", required=True, help="macbench results JSONL for the M70/BC-250")
    ap.add_argument("--row", required=True, help="matrix row id, same on both sides")
    ap.add_argument("--min-decode-ratio", type=float, default=0.9)
    ap.add_argument("--max-watts-ratio", type=float, default=1.0)
    args = ap.parse_args()

    baseline_tps, baseline_watts = row_metrics(args.baseline, args.row)
    candidate_tps, candidate_watts = row_metrics(args.candidate, args.row)
    decode_ok, watts_ok = verdict(baseline_tps, baseline_watts, candidate_tps, candidate_watts,
                                   args.min_decode_ratio, args.max_watts_ratio)

    print(f"row={args.row} baseline: decode={baseline_tps:.1f} tok/s watts={baseline_watts:.1f}")
    print(f"row={args.row} candidate: decode={candidate_tps:.1f} tok/s watts={candidate_watts:.1f}")
    print(f"decode: {'PASS' if decode_ok else 'FAIL'} "
          f"(need >= {args.min_decode_ratio * baseline_tps:.1f} tok/s)")
    print(f"watts:  {'PASS' if watts_ok else 'FAIL'} "
          f"(need <= {args.max_watts_ratio * baseline_watts:.1f} W)")

    if decode_ok and watts_ok:
        print("VERDICT: PASS - replacement verified, safe to proceed with retirement phase")
        return 0
    print("VERDICT: FAIL - do not retire the Mac Pro for this row yet")
    return 1


if __name__ == "__main__":
    sys.exit(main())
