#!/usr/bin/env python3
"""build_sample.py — pilot a7d04e5ebe7d (Complementary Memory Traces for
Multi-hop Retrieval), wq#1684.

Writes the FIXED sample the CARD.md pins by sha256. No live fleet data is
read (minerva.db is not a broker read root for a pilot, per
foreman/research/DEFER_PILOT_LANE.md "Data that must not cross to a pilot
node") — Lumen's actual memory reader (`memory/recall.py`) does top-k-newest
injection with no query-conditioned retrieval at all, so there is no live
retrieval path to extract a multi-hop eval sample FROM. This corpus is a
synthetic multi-hop QA set, deterministic (no randomness), grounded in real
fleet facts drawn from this repo's own docs so the entities are not
arbitrary, small enough to hand-verify every gold label.

Each "bridge" item is a 2-hop question answerable only by combining two
facts that share an entity string absent from the question itself; that gap
is exactly what a single verbatim-trace retriever misses and what a second,
complementary (entity/gist) trace is supposed to close. Distractor facts
share no entity with any bridge pair, so raw corpus size does not trivially
solve retrieval by itself.
"""
import hashlib
import json
import os

BRIDGES = [
    {
        "id": "hathor-down",
        "hop1": "hathor's management IP address is 100.80.71.13.",
        "hop2": "100.80.71.13 has been powered off since 2026-07-18, pending a KVM console purchase for a controlled bench boot.",
        "bridge_entity": "100.80.71.13",
        "question": "Why is the machine at hathor's management IP address currently unreachable?",
    },
    {
        "id": "qwen36-node",
        "hop1": "the qwen36-local LiteLLM route points at Ollama on 100.100.129.24.",
        "hop2": "100.100.129.24 serves live production traffic today and must never be repurposed for a pilot treatment run.",
        "bridge_entity": "100.100.129.24",
        "question": "Why can a pilot not deploy an experimental patch to the qwen36-local route's backing node?",
    },
    {
        "id": "wq1107-loop",
        "hop1": "wq#1107 documented that retrying a refused Bash command is what tripped the loop breaker.",
        "hop2": "the loop breaker fired on 8 of 9 attempts on 2026-08-29.",
        "bridge_entity": "the loop breaker",
        "question": "On what date did the failure mode wq#1107 documented actually fire repeatedly?",
    },
    {
        "id": "issue257-card",
        "hop1": "issue #257 found that the pilot card generator writes the raw EXPERIMENT_CARD.template.md with no spec filled in.",
        "hop2": "the raw template's unresolved sections are Hypothesis, Primary metric, Fixed sample, Baseline, and Treatment.",
        "bridge_entity": "the raw template",
        "question": "Which sections does issue #257's card generator defect leave unresolved?",
    },
    {
        "id": "wq1719-guard",
        "hop1": "wq#1719/wq#900 made the primary repo roots read-only for workers.",
        "hop2": "a write blocked by the primary-tree guard must be made in the worker's own worktree and landed with lumen-pr.",
        "bridge_entity": "the primary-tree guard",
        "question": "What must a worker do when a primary-tree guard blocks a write to a root that wq#1719/wq#900 made read-only?",
    },
    {
        "id": "fleet-memory-split",
        "hop1": "wq#1183 split fleet agent memory into an episodic store and a distilled store.",
        "hop2": "the distilled store lives under FLEET_MEMORY_ROOT, default /home/lumen/fleet-memory, as git/markdown facts.",
        "bridge_entity": "the distilled store",
        "question": "Where on disk does the durable half of the memory split wq#1183 introduced actually live?",
    },
    {
        "id": "kv-cache-pilot",
        "hop1": "pilot 2c11123fab8b tested adaptive KV cache filtering and recommended SKIP.",
        "hop2": "SKIP was chosen because no drop-in adaptive KV filtering exists for llama.cpp/Ollama, the engine Lumen actually runs.",
        "bridge_entity": "SKIP",
        "question": "Why did the adaptive KV cache filtering pilot recommend against adoption?",
    },
    {
        "id": "reach-ts-gap",
        "hop1": "wq#1877 found that reach.ts never paired a crossing sibling for a warp in an unreachable state.",
        "hop2": "the unpaired door belonged to object 11, a STAY guard on global flag 9, whose real unlock is BillsHouseBillSSTicketText on Route 25.",
        "bridge_entity": "the unpaired door",
        "question": "What NPC and story flag actually gate the door that reach.ts failed to pair a crossing sibling for?",
    },
]

DISTRACTORS = [
    "the dispatcher's max_per_day for the pilot lane is 3.",
    "recall.py is stdlib-only: no embeddings, no FTS, recency plus scope filter.",
    "distill.py extracts at most 5 validated facts per episode.",
    "FLEET_MEMORY_MODEL defaults to qwen36-local so distillation never spends Anthropic quota.",
    "a pilot report must end with a RECOMMENDATION: ADOPT, WATCH, or SKIP line.",
    "the fleet-exec policy allows read-only run plus copy into pilots on hathor.",
    "minerva.db is never copied to a pilot node because credentials are mirrored into its context sections.",
    "wq#912 refuses wq add --lane pilot without a dispatcher/config.json entry for that lane.",
    "the reaper re-runs a defer note once before retiring it on a second missing RECOMMENDATION line.",
    "router.py biases the pilot lane toward probe-build first, research-synthesis second.",
    "instinct tier 0 rule 2 names Thor, 100.112.151.100, as never to be rebooted without permission.",
    "instinct tier 0 rule 4 requires masking any command that can emit a credential before it runs.",
    "Bash output in this harness is capped at 2000 characters per result.",
    "a full ps aux sweep is off the allowlist because it can print other processes' argv.",
    "the pilots directory's .gitignore excludes sample.json and graph.db as regenerable artifacts.",
    "git push --force is on the house deny-list enforced by the PreToolUse guard hook.",
    "wq done requires a PR url plus a one-line summary in its --notes argument.",
    "a Stop hook refuses to end a session while evidence.json is missing required criteria.",
    "acceptance.json is pinned by hash at dispatch and cannot be edited by the worker.",
    "wq#1183's episodic store is append-only, written by fworker at attempt end.",
    "the explore-exploit prompt sampling pilot is proposal 9d95766f5f43.",
    "the economy-vs-no-economy allocation pilot is proposal 7187e39f4cbf and landed as a feat commit.",
    "LPP's OBJECTIVE_DRIFT mode reads a lower badge count at the time of a ledger win to detect a cleared gym.",
    "probe_pocket1870 checks 28 to 29 conditions depending on which map3 checkpoint is on disk that run.",
    "wq#1870 gave warp views an otherSide field naming a sibling door with the same destination.",
]


def build():
    facts = []
    gold = {}
    questions = []
    fid = 0
    for b in BRIDGES:
        f1, f2 = fid, fid + 1
        facts.append({"id": f1, "text": b["hop1"]})
        facts.append({"id": f2, "text": b["hop2"]})
        fid += 2
        questions.append({
            "id": b["id"],
            "question": b["question"],
            "gold_fact_ids": [f1, f2],
            "bridge_entity": b["bridge_entity"],
        })
    for d in DISTRACTORS:
        facts.append({"id": fid, "text": d})
        fid += 1
    return {"facts": facts, "questions": questions}


def main():
    sample = build()
    out_path = os.path.join(os.path.dirname(__file__), "sample.json")
    blob = json.dumps(sample, indent=2, sort_keys=True)
    with open(out_path, "w") as f:
        f.write(blob)
    digest = hashlib.sha256(blob.encode()).hexdigest()
    print(f"wrote {out_path}")
    print(f"facts={len(sample['facts'])} questions={len(sample['questions'])}")
    print(f"sha256={digest}")


if __name__ == "__main__":
    main()
