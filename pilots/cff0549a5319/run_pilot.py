"""Pilot cff0549a5319 (wq#1901): DAG-based reasoning consensus vs. single-call baseline.

Baseline: one LLM call per fixture item, temperature 0.7.
Treatment: TRACES_PER_ITEM LLM calls per item at temperature 0.9, aggregated with a
weighted-DAG majority vote: each trace is a node, an edge (weight 1) is added between
every pair of traces whose normalized final answer matches, and the consensus answer is
the normalized answer of the node with the highest summed edge weight (ties broken by
first occurrence). This is the smallest thing that tests the proposal's hypothesis
(more reasoning traces + agreement-weighted aggregation beat one shot) without building
the full lumen-reasoning-consensus service.

Both arms run against the same fixed sample (fixture.json) and the same model
(hathor-llama8b, so the pilot runs on hathor per the experiment card) via the LiteLLM
gateway.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "despag"))
from backends.litellm import read_litellm_key, LITELLM_BASE_URL  # noqa: E402

MODEL = "hathor-llama8b"
TRACES_PER_ITEM = 3
HERE = Path(__file__).resolve().parent


def call_model(prompt: str, temperature: float) -> str:
    key = read_litellm_key()
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": 32,
    }).encode()
    req = urllib.request.Request(
        LITELLM_BASE_URL + "/v1/chat/completions",
        data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    return data["choices"][0]["message"]["content"]


def normalize(text: str) -> str:
    m = re.search(r"-?\d+(\.\d+)?", text.replace(",", ""))
    return m.group(0) if m else text.strip().lower()


def dag_consensus(traces: list[str]) -> str:
    """Weighted-DAG majority vote: nodes = traces, edge weight = agreement count."""
    norm = [normalize(t) for t in traces]
    weight = {i: sum(1 for j in range(len(norm)) if i != j and norm[j] == norm[i]) for i in range(len(norm))}
    best = max(range(len(norm)), key=lambda i: weight[i])
    return norm[best]


def run():
    fixture = json.loads((HERE / "fixture.json").read_text())["items"]

    t0 = time.time()
    baseline_correct = 0
    baseline_results = []
    for item in fixture:
        raw = call_model(item["prompt"], temperature=0.7)
        got = normalize(raw)
        ok = got == item["answer"]
        baseline_correct += ok
        baseline_results.append({"id": item["id"], "got": got, "expected": item["answer"], "correct": ok})
    baseline_wall_s = time.time() - t0

    t0 = time.time()
    treatment_correct = 0
    treatment_results = []
    for item in fixture:
        traces = [call_model(item["prompt"], temperature=0.9) for _ in range(TRACES_PER_ITEM)]
        got = dag_consensus(traces)
        ok = got == item["answer"]
        treatment_correct += ok
        treatment_results.append({"id": item["id"], "traces": [normalize(t) for t in traces], "got": got, "expected": item["answer"], "correct": ok})
    treatment_wall_s = time.time() - t0

    out = {
        "model": MODEL,
        "n": len(fixture),
        "traces_per_item": TRACES_PER_ITEM,
        "baseline": {"accuracy": baseline_correct / len(fixture), "wall_s": baseline_wall_s, "results": baseline_results},
        "treatment": {"accuracy": treatment_correct / len(fixture), "wall_s": treatment_wall_s, "results": treatment_results},
    }
    (HERE / "results.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({k: v for k, v in out.items() if k in ("model", "n", "traces_per_item")} | {
        "baseline_accuracy": out["baseline"]["accuracy"],
        "treatment_accuracy": out["treatment"]["accuracy"],
        "baseline_wall_s": round(baseline_wall_s, 1),
        "treatment_wall_s": round(treatment_wall_s, 1),
    }))


if __name__ == "__main__":
    run()
