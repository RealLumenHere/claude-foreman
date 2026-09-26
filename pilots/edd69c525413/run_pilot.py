#!/usr/bin/env python3
"""run_pilot.py — pilot edd69c525413 (wq#1935). Baseline FIRST, then
treatment, same fixed sample (sample.json, sha256 logged in results.json).
Stdlib + memory/cmt_retrieval.py + memory/role_retrieval.py only — no
network, no fleet-exec.

Node substitution (disclosed, same defect/fix as wq#1570/a7d04e5ebe7d,
`research/DEFER_PILOT_LANE.md:76`): `wq show 1935` has `needs:` empty, so
`fleet-exec grant` denies with `grant:grant_missing_env` regardless of
`fleet-exec-policy.yaml` already allowing hathor — there is no live grant to
exec on hathor with. This harness needs no compute beyond stdlib string
matching, so it runs on Lumen; both arms run in the same process against the
same pinned sample, so the relative result (treatment vs baseline) is not
affected by which node issues the calls.
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "memory"))
import role_retrieval  # noqa: E402

HERE = os.path.dirname(__file__)
SAMPLE_PATH = os.path.join(HERE, "sample.json")
K = 2


def load_sample():
    with open(SAMPLE_PATH) as f:
        blob = f.read()
    digest = hashlib.sha256(blob.encode()).hexdigest()
    return json.loads(blob), digest


def score(retrieve_fn, sample):
    """Primary metric: contamination_rate -- the fraction of queries whose
    top-k retrieved set includes at least one fact from a role OTHER than
    the query's target role (e.g. a procedural how-to note surfacing in
    answer to an episodic "what happened" question). Role separation's
    claimed benefit is keeping cross-role noise out of what gets returned,
    not necessarily improving whether the single gold fact ranks first --
    recall_at_gold is logged too, as a secondary check that the role filter
    does not lose the gold fact in the process."""
    by_id = {f["id"]: f for f in sample["facts"]}
    contaminated = 0
    gold_hits = 0
    per_question = []
    for q in sample["questions"]:
        retrieved = retrieve_fn(by_id.values(), q)
        gold = set(q["gold_fact_ids"])
        got_gold = gold.issubset(set(retrieved))
        gold_hits += int(got_gold)
        off_role = [
            fid for fid in retrieved
            if role_retrieval.classify_role(by_id[fid]["text"]) != q["role"]
        ]
        is_contaminated = bool(off_role)
        contaminated += int(is_contaminated)
        per_question.append({
            "id": q["id"],
            "retrieved": retrieved,
            "gold": sorted(gold),
            "gold_retrieved": got_gold,
            "off_role_in_result": off_role,
        })
    n = len(sample["questions"])
    return {
        "contamination_rate": contaminated / n if n else 0.0,
        "recall_at_gold": gold_hits / n if n else 0.0,
        "n": n,
        "per_question": per_question,
    }


def baseline_retrieve(facts, q):
    return role_retrieval.retrieve_with_role_filter(facts, q["question"], K, role=None)


def treatment_retrieve(facts, q):
    return role_retrieval.retrieve_with_role_filter(facts, q["question"], K, role=q["role"])


def main():
    sample, digest = load_sample()

    classifications = {
        f["id"]: role_retrieval.classify_role(f["text"]) for f in sample["facts"]
    }

    baseline = score(baseline_retrieve, sample)
    treatment = score(treatment_retrieve, sample)

    results = {
        "sample_sha256": digest,
        "k": K,
        "classifications": classifications,
        "baseline": baseline,
        "treatment": treatment,
        "delta_contamination_rate": (
            treatment["contamination_rate"] - baseline["contamination_rate"]
        ),
        "delta_recall_at_gold": (
            treatment["recall_at_gold"] - baseline["recall_at_gold"]
        ),
    }

    out_path = os.path.join(HERE, "results.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(json.dumps({
        "baseline_contamination_rate": baseline["contamination_rate"],
        "treatment_contamination_rate": treatment["contamination_rate"],
        "delta_contamination_rate": results["delta_contamination_rate"],
        "baseline_recall_at_gold": baseline["recall_at_gold"],
        "treatment_recall_at_gold": treatment["recall_at_gold"],
        "sample_sha256": digest,
    }, indent=2))


if __name__ == "__main__":
    main()
