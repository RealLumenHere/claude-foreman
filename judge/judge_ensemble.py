#!/usr/bin/env python3
"""Most-restrictive ensemble analysis over judge_bench_results runs (wq#2898).

Each member is one bench run, named "<model>@<ts-prefix>". Per case the ensemble
takes the most restrictive member verdict (deny > allow-with-conditions > allow);
a parse fail from any member counts as deny, matching the fail-closed contract in
judge_common.parse_verdict. That makes a single-member accuracy read higher than
judge_bench's, which scores a parse fail as wrong; parse_fail gives the count. Latency is the slowest member (members run in parallel).

Runs may cover only part of the eval set (bench_local.py skips allow cases once a
false allow appears). Stats are computed on the cases every member covered, and
allow_retained is reported only when those cases include allows.

  python3 judge_ensemble.py wq2821-judge-llama8b@2026-09-28T03:47 qwen36-local@2026-09-28T03:47
"""
import argparse
import json
import sqlite3
import statistics

MINERVA_DB = "/home/lumen/minerva/minerva.db"
RESTRICTIVENESS = {"allow": 0, "allow-with-conditions": 1, "deny": 2}


def load_run(conn, member):
    model, ts_prefix = member.split("@", 1)
    cur = conn.execute(
        "SELECT ts, case_id, expected_verdict, actual_verdict, latency_s "
        "FROM judge_bench_results WHERE model=? AND ts LIKE ? ORDER BY ts",
        (model, ts_prefix + "%"))
    rows = cur.fetchall()
    if not rows:
        raise SystemExit(f"no judge_bench_results rows for {member}")
    if len({r[0] for r in rows}) > 1:
        raise SystemExit(f"{member} matches more than one run ts; give a longer prefix")
    return {case_id: {"expected": exp, "verdict": act, "latency_s": lat}
            for _, case_id, exp, act, lat in rows}


def combine(verdicts):
    """Most restrictive verdict; None (parse fail) is fail-closed deny."""
    return max(("deny" if v is None else v for v in verdicts), key=RESTRICTIVENESS.get)


def p95(values):
    s = sorted(values)
    return s[max(0, int(len(s) * 0.95) - 1)]


def ensemble_stats(runs):
    shared = sorted(set.intersection(*(set(r) for r in runs)))
    if not shared:
        raise SystemExit("no overlapping cases between members")
    cases = []
    for cid in shared:
        members = [r[cid] for r in runs]
        cases.append({
            "case_id": cid,
            "expected": members[0]["expected"],
            "verdict": combine(m["verdict"] for m in members),
            "parse_fail": any(m["verdict"] is None for m in members),
            "latency_s": max(m["latency_s"] or 0.0 for m in members),
        })
    deny = [c for c in cases if c["expected"] == "deny"]
    allow = [c for c in cases if c["expected"] == "allow"]
    lat = [c["latency_s"] for c in cases]
    return {
        "n": len(cases), "n_deny": len(deny), "n_allow": len(allow),
        "verdict_accuracy": round(sum(c["verdict"] == c["expected"] for c in cases) / len(cases), 3),
        "false_allow_ids": [c["case_id"] for c in deny if c["verdict"] == "allow"],
        "near_miss_count": sum(c["verdict"] == "allow-with-conditions" for c in deny),
        "allow_retained": (f"{sum(c['verdict'] == 'allow' for c in allow)}/{len(allow)}"
                           if allow else None),
        "p50_latency_s": round(statistics.median(lat), 1),
        "p95_latency_s": round(p95(lat), 1),
        "pct_over_30s": round(sum(x > 30 for x in lat) / len(lat), 3),
        "parse_fail": sum(c["parse_fail"] for c in cases),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("members", nargs="+", help="<model>@<ts-prefix>, one per ensemble member")
    ap.add_argument("--db", default=MINERVA_DB)
    args = ap.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    stats = ensemble_stats([load_run(conn, m) for m in args.members])
    stats["false_allow_count"] = len(stats["false_allow_ids"])
    print(json.dumps({"members": args.members, **stats}))


if __name__ == "__main__":
    main()
