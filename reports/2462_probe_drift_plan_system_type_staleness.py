#!/usr/bin/env python3
"""reports/2462_probe_drift_plan_system_type_staleness.py

wq#2462 verify-pass on wq#2434 / PR #885 ("drift planner JSON schema").

Break attempt: does drift_plan.py's per-rung `system_type` track the live
LiteLLM config on regeneration, as ops/wq2434-drift-plan/README.md:69-71
documents ("system_type is chat unless the LiteLLM entry declares
mode: embedding")? Or does the merge-not-overwrite regen path (build_plan(),
drift_plan.py:257-290) freeze it at whatever the first-ever plan file said?

Imports the shipped, unmodified module. Touches only a tmp dir; no network
or systemd state. Two `generate()` calls simulate two weekly timer firings
against the same rung, `svc-x`, whose LiteLLM entry is redeployed from a
plain chat model to an embedding backend between the two runs.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, "/home/lumen/foreman/ops/wq2434-drift-plan")
import drift_plan as dp  # noqa: E402


def main():
    tmp = tempfile.mkdtemp(prefix="wq2462-probe-")
    cfg_path = os.path.join(tmp, "litellm_config.yaml")
    out_dir = os.path.join(tmp, "plans")
    os.makedirs(out_dir, exist_ok=True)

    # Run 1: svc-x is a plain chat rung.
    with open(cfg_path, "w") as f:
        json.dump({"model_list": [{"model_name": "svc-x",
                                    "model_info": {"mode": "chat"}}]}, f)
    plans1, _, _ = dp.generate(cfg_path, out_dir, "")
    for p in plans1:
        dp.write_json(os.path.join(out_dir, f"{p['rung']}.json"), p)
    p1 = plans1[0]
    print(f"run 1 (config: mode=chat)      -> system_type={p1['system_type']!r} "
          f"metrics={sorted(p1['metrics'])}")

    # Run 2 (next weekly timer firing): svc-x's LiteLLM entry is redeployed as
    # an embedding backend. classify() will read "rag" from the live config.
    with open(cfg_path, "w") as f:
        json.dump({"model_list": [{"model_name": "svc-x",
                                    "model_info": {"mode": "embedding"}}]}, f)
    plans2, _, _ = dp.generate(cfg_path, out_dir, "")
    p2 = plans2[0]
    print(f"run 2 (config: mode=embedding) -> system_type={p2['system_type']!r} "
          f"metrics={sorted(p2['metrics'])}")

    rag_only = {"retrieval_recall", "grounding_rate", "corpus_update_lag_h"}
    stale = p2["system_type"] != "rag"
    missing_rag_metrics = not (rag_only & set(p2["metrics"]))

    print()
    if stale and missing_rag_metrics:
        print("STALE: system_type did not follow the live config's mode:embedding "
              "on regeneration; RAG metrics (retrieval_recall, grounding_rate, "
              "corpus_update_lag_h) never attached. README.md:69-71's documented "
              "behavior holds only on a rung's FIRST generation.")
        return 1
    print("OK: system_type tracked the live config across regeneration.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
