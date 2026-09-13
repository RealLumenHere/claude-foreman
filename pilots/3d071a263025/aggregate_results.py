"""Aggregate OfficeVal verifier scores for pilot 3d071a263025 (wq#1823)."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def load(name):
    return json.loads((HERE / name).read_text())


def aggregate(results):
    total = sum(r["total_score"] for r in results)
    maxs = sum(r["max_score"] for r in results)
    return total, maxs, total / maxs if maxs else 0.0


def main():
    baseline = [load(f"baseline_{t}.json") for t in ("002", "005", "010")]
    treatment = [load(f"treatment_{t}.json") for t in ("005",)]

    b_total, b_max, b_ratio = aggregate(baseline)
    t_total, t_max, t_ratio = aggregate(treatment)

    print(f"baseline:  {b_total}/{b_max} = {b_ratio:.3f} (tasks 002,005,010)")
    print(f"treatment: {t_total}/{t_max} = {t_ratio:.3f} (task 005 only)")


if __name__ == "__main__":
    main()
