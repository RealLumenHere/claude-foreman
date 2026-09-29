#!/usr/bin/env python3
"""run_pilot.py -- pilot 1a025ddf9834 (wq#1951) runner. Runs baseline (plain
target-model call) then treatment (draft-then-verify, spec_decode.speculative_call)
against the same fixed prompt sample, and prints a JSON summary: mean/median
latency for each arm, speedup, and text-match rate (quality-parity proxy).

Fixed sample: prompts.json in this directory (committed, not live/mutable).
"""
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from spec_decode import baseline_call, speculative_call  # noqa: E402
from judge_common import litellm_key  # noqa: E402


def run():
    sample_path = os.path.join(os.path.dirname(__file__), "prompts.json")
    prompts = json.load(open(sample_path))
    key = litellm_key()

    baseline_results, treatment_results = [], []
    errors = []
    for p in prompts:
        try:
            baseline_results.append(baseline_call(p, key=key))
        except Exception as e:
            errors.append({"arm": "baseline", "prompt": p, "error": str(e)})
        try:
            treatment_results.append(speculative_call(p, key=key))
        except Exception as e:
            errors.append({"arm": "treatment", "prompt": p, "error": str(e)})

    if errors:
        print(json.dumps({"status": "baseline_or_treatment_failed",
                           "errors": errors}, indent=2))
        sys.exit(1)

    b_lat = [r["latency_s"] for r in baseline_results]
    t_lat = [r["latency_s"] for r in treatment_results]
    matches = sum(
        1 for b, t in zip(baseline_results, treatment_results)
        if b["text"].strip() == t["text"].strip()
    )
    summary = {
        "n": len(prompts),
        "baseline_mean_s": statistics.mean(b_lat),
        "baseline_median_s": statistics.median(b_lat),
        "treatment_mean_s": statistics.mean(t_lat),
        "treatment_median_s": statistics.median(t_lat),
        "speedup_mean": statistics.mean(b_lat) / statistics.mean(t_lat),
        "exact_match_rate": matches / len(prompts),
        "accepted_verbatim_rate": sum(
            1 for r in treatment_results if r["accepted_verbatim"]
        ) / len(prompts),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    run()
