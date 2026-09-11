#!/usr/bin/env python3
"""Deterministic fixed sample for pilot 7187e39f4cbf.

Generates a pool of "research actions" (cost in tokens, value in expected
research-output quality units) that 3 fleet-researcher agents (cerebras,
mistral, openrouter) could each choose to run. Both arms (baseline / economy)
see this exact same pool and the exact same total shared token budget.
"""
import json
import hashlib
import random
import sys

SEED = 1683
N_TASKS = 60
AGENTS = ["cerebras", "mistral", "openrouter"]
TOTAL_BUDGET = 100  # shared token budget, split evenly under baseline (no economy)


def build():
    rng = random.Random(SEED)
    tasks = []
    for i in range(N_TASKS):
        cost = rng.randint(1, 12)
        # value is noisy w.r.t. cost so higher cost isn't always higher value -
        # this is what makes naive first-fit differ from ratio-ranked allocation
        value = round(cost * rng.uniform(0.3, 2.5), 3)
        # each task is "visible" to exactly one agent first (its home researcher),
        # modeling today's purely task-driven routing with no cross-agent signaling
        home_agent = AGENTS[i % len(AGENTS)]
        tasks.append({"id": i, "cost": cost, "value": value, "home_agent": home_agent})
    sample = {
        "seed": SEED,
        "agents": AGENTS,
        "total_budget": TOTAL_BUDGET,
        "tasks": tasks,
    }
    return sample


def main():
    sample = build()
    blob = json.dumps(sample, sort_keys=True, indent=2)
    sha = hashlib.sha256(blob.encode()).hexdigest()
    sample["sha256"] = sha
    out_path = sys.argv[1] if len(sys.argv) > 1 else "sample.json"
    with open(out_path, "w") as f:
        json.dump(sample, f, indent=2)
    print(f"wrote {out_path} sha256={sha} tasks={len(sample['tasks'])}")


if __name__ == "__main__":
    main()
