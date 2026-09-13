#!/usr/bin/env python3
"""run_pilot.py — pilot a7d04e5ebe7d (wq#1684). Baseline FIRST, then
treatment, same fixed sample (sample.json, built by build_sample.py, sha256
pinned in CARD.md). Writes results.json. Stdlib + memory/cmt_retrieval.py
only — no network, no fleet-exec, both arms run on Lumen (node substitution
disclosed in REPORT.md per ISSUE-LOG #257/#33 precedent: hathor has been
physically down since 2026-07-18).
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "memory"))
import cmt_retrieval  # noqa: E402

HERE = os.path.dirname(__file__)
SAMPLE_PATH = os.path.join(HERE, "sample.json")
K = 4


def load_sample():
    with open(SAMPLE_PATH) as f:
        blob = f.read()
    digest = hashlib.sha256(blob.encode()).hexdigest()
    return json.loads(blob), digest


def score(retrieve_fn, sample):
    facts = sample["facts"]
    hits = 0
    per_question = []
    for q in sample["questions"]:
        retrieved = retrieve_fn(facts, q["question"], K)
        gold = set(q["gold_fact_ids"])
        got_both = gold.issubset(set(retrieved))
        hits += int(got_both)
        per_question.append({
            "id": q["id"],
            "retrieved": retrieved,
            "gold": sorted(gold),
            "both_gold_retrieved": got_both,
        })
    n = len(sample["questions"])
    return {
        "recall_at_bridge": hits / n if n else 0.0,
        "hits": hits,
        "n": n,
        "per_question": per_question,
    }


def main():
    sample, digest = load_sample()

    baseline = score(cmt_retrieval.retrieve_single_trace, sample)
    treatment = score(cmt_retrieval.retrieve_complementary_traces, sample)

    results = {
        "sample_sha256": digest,
        "k": K,
        "baseline": baseline,
        "treatment": treatment,
        "delta_recall_at_bridge": treatment["recall_at_bridge"] - baseline["recall_at_bridge"],
    }

    out_path = os.path.join(HERE, "results.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, sort_keys=True)

    print(f"sample_sha256={digest}")
    print(f"baseline recall_at_bridge = {baseline['recall_at_bridge']:.3f} ({baseline['hits']}/{baseline['n']})")
    print(f"treatment recall_at_bridge = {treatment['recall_at_bridge']:.3f} ({treatment['hits']}/{treatment['n']})")
    print(f"delta = {results['delta_recall_at_bridge']:.3f}")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
