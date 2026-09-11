#!/usr/bin/env python3
"""Pilot runner for 7187e39f4cbf: does a minimal token economy improve
agent coordination vs today's purely task-driven routing?

baseline (today): total budget is split evenly across agents up front, no
    cross-agent signaling. Each agent sees only its own home tasks and picks
    them in a fixed arbitrary order (task id order = arrival order) until its
    own sub-budget runs out. This matches the proposal's "current state":
    agents are orchestrated task-driven with no token-based signaling or
    budget allocation.

treatment (economy): a single shared pool of tokens. A minimal reputation/
    compute-credit market: every task from every agent is ranked by
    value/cost ratio (the price signal a token economy provides) and tokens
    are spent top-ratio-first against the shared pool regardless of which
    agent owns the task. This is the smallest mechanism that matches the
    proposal ("agents query the economy service for budget constraints
    before selecting research actions") without building the systemd
    service, timer, or LLM-simulated dynamics the full proposal calls for.
"""
import argparse
import json


def run_baseline(sample):
    agents = sample["agents"]
    total_budget = sample["total_budget"]
    per_agent_budget = total_budget / len(agents)
    tasks_by_agent = {a: [] for a in agents}
    for t in sample["tasks"]:
        tasks_by_agent[t["home_agent"]].append(t)
    for a in agents:
        tasks_by_agent[a].sort(key=lambda t: t["id"])

    completed = []
    for a in agents:
        remaining = per_agent_budget
        for t in tasks_by_agent[a]:
            if t["cost"] <= remaining:
                completed.append(t["id"])
                remaining -= t["cost"]
    return completed


def run_treatment(sample):
    total_budget = sample["total_budget"]
    tasks = list(sample["tasks"])
    tasks.sort(key=lambda t: (t["value"] / t["cost"]), reverse=True)

    completed = []
    remaining = total_budget
    for t in tasks:
        if t["cost"] <= remaining:
            completed.append(t["id"])
            remaining -= t["cost"]
    return completed


def optimal_fractional(sample):
    """Theoretical ceiling (fractional knapsack) used only to normalize the
    reported percentage; not itself achievable by either arm since tasks are
    all-or-nothing here."""
    total_budget = sample["total_budget"]
    tasks = sorted(sample["tasks"], key=lambda t: (t["value"] / t["cost"]), reverse=True)
    remaining = total_budget
    value = 0.0
    for t in tasks:
        if t["cost"] <= remaining:
            value += t["value"]
            remaining -= t["cost"]
        else:
            value += t["value"] * (remaining / t["cost"])
            remaining = 0
            break
    return value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["baseline", "treatment"])
    ap.add_argument("--sample", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.sample) as f:
        sample = json.load(f)

    by_id = {t["id"]: t for t in sample["tasks"]}
    total_value = sum(t["value"] for t in sample["tasks"])
    opt = optimal_fractional(sample)

    if args.arm == "baseline":
        completed = run_baseline(sample)
    else:
        completed = run_treatment(sample)

    value_captured = sum(by_id[i]["value"] for i in completed)
    cost_spent = sum(by_id[i]["cost"] for i in completed)

    result = {
        "arm": args.arm,
        "sample_sha256": sample["sha256"],
        "n_tasks_total": len(sample["tasks"]),
        "n_tasks_completed": len(completed),
        "total_budget": sample["total_budget"],
        "cost_spent": cost_spent,
        "value_captured": round(value_captured, 3),
        "total_value_available": round(total_value, 3),
        "fractional_optimum": round(opt, 3),
        "pct_of_optimum": round(100 * value_captured / opt, 2) if opt else 0.0,
        "completed_task_ids": completed,
    }
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
