#!/usr/bin/env python3
"""Baseline for pilot 76c39dd5afde: outcome and failure-cause rates of the live
fleet researchers (lumen-fleet-{openrouter,cerebras,mistral}) from their journal.

Input is a pinned journal extract (see CARD.md "Fixed sample"); output is JSON.
Usage: measure_baseline.py <journal.log> > baseline.json
"""
import json
import re
import sys
from collections import Counter

GROUND = re.compile(r"grounding: (pass|fail) unresolved=(\d+) urls=(\d+) unseen=(\d+)")
QGATE_LEN = re.compile(r"quality_gate: fail \(len=(\d+)\)")
DONE = re.compile(r"\] Done \(graph\): (.+)$")
REQUEUE = re.compile(r"mark_requeue: '([^']+)'")
REJECT = re.compile(r"write_rejected: (\S+) -> .*\((\d+) unresolved\)")
RETRIEVAL = re.compile(r"local_retrieval: injected (\d+) chars")

# A response under this many chars is a degenerate/empty completion (provider
# fault), not a research-strategy failure a context snippet could fix.
DEGENERATE_LEN = 200


def measure(lines):
    ground = Counter()
    ground_fail_kind = Counter()
    qgate_lens = []
    done, requeued, rejected = set(), set(), {}
    retrieval_chars = []
    web = Counter()
    for line in lines:
        if m := GROUND.search(line):
            ground[m[1]] += 1
            if m[1] == "fail":
                ground_fail_kind["unresolved_citation" if int(m[2]) else "other"] += 1
        elif m := QGATE_LEN.search(line):
            qgate_lens.append(int(m[1]))
        elif m := DONE.search(line):
            done.add(m[1].strip())
        elif m := REQUEUE.search(line):
            requeued.add(m[1])
        elif m := REJECT.search(line):
            rejected[m[1]] = int(m[2])
        elif m := RETRIEVAL.search(line):
            retrieval_chars.append(int(m[1]))
        elif "web_search:" in line:
            web["no_augmentation" if "no augmentation" in line else "augmented"] += 1

    degenerate = sum(1 for n in qgate_lens if n < DEGENERATE_LEN)
    attempts = sum(ground.values()) + len(qgate_lens)
    # Failed attempts a prompt-context snippet could plausibly address: grounding
    # failures (bad/unresolved citations) and non-degenerate quality-gate fails.
    addressable = ground["fail"] + (len(qgate_lens) - degenerate)
    retrieval_chars.sort()
    return {
        "attempts_scored": attempts,
        "grounding": dict(ground),
        "grounding_fail_kind": dict(ground_fail_kind),
        "grounding_first_pass_rate": round(ground["pass"] / max(1, sum(ground.values())), 3),
        "quality_gate_fail": len(qgate_lens),
        "quality_gate_fail_degenerate_lt200": degenerate,
        "tasks_done_unique": len(done),
        "tasks_requeued_unique": len(requeued),
        "tasks_rejected_unique": len(rejected),
        "rejected_with_unresolved_gt0": sum(1 for v in rejected.values() if v),
        "task_success_rate": round(len(done) / max(1, len(done) + len(requeued) + len(rejected)), 3),
        "failed_attempts": ground["fail"] + len(qgate_lens),
        "addressable_failed_attempts": addressable,
        "addressable_share_of_attempts": round(addressable / max(1, attempts), 3),
        "local_retrieval_injections": len(retrieval_chars),
        "local_retrieval_chars_median": retrieval_chars[len(retrieval_chars) // 2] if retrieval_chars else 0,
        "web_search": dict(web),
    }


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8", errors="replace") as fh:
        print(json.dumps(measure(fh), indent=2))
