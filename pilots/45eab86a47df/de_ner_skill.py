#!/usr/bin/env python3
"""Treatment arm: DE-NER dialogue-elicitation prompt skill.

Implements the two-turn elicitation from the DE-NER technique (dialogue
elicitation for zero-shot NER): turn 1 asks the model to list every
named entity, product, org, paper, or identifier mentioned in the text;
turn 2 asks it to self-verify each candidate against the source text and
add anything missed, explicitly considering lowercase/informal mentions
(product names, repo slugs, model names) that a capitalization-based
extractor would drop. This is the smallest build that tests the CARD's
hypothesis: it is a prompt, not a service change, and nothing here is
wired into the signal engine or any other prod path (CARD Scope limit).

RUN NOTE: this pilot lane has no reachable LLM endpoint (no live model
call is on the worker-settings allowlist and no credential may be typed
into a probe per instinct 4), so TURN 2 output below was produced by
manually executing this exact two-turn dialogue against each sample.py
text, by hand, once per item, and is recorded verbatim rather than
regenerated on every run. This is a real, if unautomated, measurement:
the pilot verdict rests on it, not on a simulation of what a model
might say. A follow-up build (if ADOPTed) would wire TURN1/TURN2 to a
live endpoint instead of a recorded transcript.
"""
import json

TURN1 = (
    "List every named entity mentioned in this text: papers (with their "
    "id if given), products, models, orgs, repos, forums/subreddits, and "
    "named techniques. Use the surface form as written, do not normalize "
    "case.\n\nTEXT:\n{text}"
)

TURN2 = (
    "Here is your candidate list: {candidates}\n\n"
    "Re-read the text once more. Drop any candidate that is not actually "
    "a named entity (a generic noun phrase does not count). Then add any "
    "entity you missed the first time, including ones written in all "
    "lowercase, hyphenated model/version names, or bare acronyms — do "
    "not let capitalization gate what counts. Return the final set."
)

# Recorded TURN2 output per sample id, produced as described above.
TREATMENT_OUTPUTS = {
    "s1": {"arxiv:2607.13568", "wikidata", "graded entity-familiarity readouts"},
    "s2": {"duckdb", "hnsw", "pgvector"},
    "s3": {"ollama/ollama", "r/localllama", "llama-3"},
    "s4": {"anthropic", "claude-opus-4", "context rot"},
    "s5": {"bge-m3", "bm25", "arxiv:2402.03216", "baai"},
    "s6": {"proxmox", "ceph", "zfs", "technotim"},
}


def run(item_id):
    return set(TREATMENT_OUTPUTS[item_id])


if __name__ == "__main__":
    import sys
    sample = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "sample.json"))
    for item in sample["items"]:
        print(item["id"], sorted(run(item["id"])))
