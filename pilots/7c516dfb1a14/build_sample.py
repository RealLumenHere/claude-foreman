#!/usr/bin/env python3
"""Build the fixed synthetic sample for pilot 7c516dfb1a14.

No fleet extract of minerva.db / lumen-research-index was reachable from this
pilot worktree's allowed directories (see CARD.md "Fixed sample" note), so the
sample is synthetic-but-representative: shapes mirror what minerva_memory.py
(archival memory) and lumen-research-index (research doc metadata) store today
-- id, kind, tags, created_at, content, source -- generated deterministically.
"""
import hashlib
import json
import random
import sys

SEED = 1568
N_MEMORY = 2500
N_RESEARCH = 2500
TAGS = [f"tag{i:02d}" for i in range(40)]


def build():
    rng = random.Random(SEED)
    records = []
    rid = 0
    for kind, n in (("archival_memory", N_MEMORY), ("research_doc", N_RESEARCH)):
        for _ in range(n):
            rid += 1
            n_tags = rng.randint(1, 4)
            tags = sorted(rng.sample(TAGS, n_tags))
            records.append({
                "id": f"{kind[:3]}-{rid:06d}",
                "kind": kind,
                "tags": tags,
                "created_at": 1700000000 + rng.randint(0, 60_000_000),
                "content": f"synthetic {kind} body {rid}",
                "source": rng.choice(["agent", "operator", "ingest"]),
            })
    rng.shuffle(records)
    return records


def main():
    records = build()
    payload = json.dumps(records, sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    out = {"seed": SEED, "count": len(records), "sha256": digest, "records": records}
    with open("sample.json", "w") as f:
        json.dump(out, f)
    print(f"wrote sample.json: {len(records)} records sha256={digest}", file=sys.stderr)


if __name__ == "__main__":
    main()
