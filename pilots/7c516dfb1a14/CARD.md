# PILOT 7c516dfb1a14 — Add Agent‑Centric Data Store for Archival Memory and Research Index

Experiment card. Generated 2026-09-09T09:10:18+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `7c516dfb1a14` |
| Deferred | 2026-07-07 |
| Impact / effort / risk | M / M / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/7c516dfb1a14/REPORT.md` |

## Why this is a pilot and not a research prompt

reopened wq#1559 (form failure, never judged): Potential benefits are unclear without a measurement harness; we should prototype and benchmark before committing.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

Unifying archival-memory records and research-index document metadata into one
agent-centric SQLite+JSON table removes the app-level merge step that today's
split schema (separate normalized tables for memory vs. research docs) requires
for a cross-store "find by tag" query, and the unified query's p50 latency is
therefore lower than the split-schema baseline's p50 latency by at least 15% on
the same fixed sample. Falsifier: if unified p50 is not lower than baseline p50
by >=15%, the JSON-column scan cost outweighs the merge savings and the answer is
SKIP or WATCH, not ADOPT.

## Primary metric

**p50 latency (ms) of a cross-kind tag lookup** — "return the 20 most recent
records (either archival-memory or research-doc) tagged X", timed end-to-end
including any Python-side merge, over 200 randomized tag queries. Win condition,
fixed before the run: treatment p50 <= 0.85 * baseline p50.

## Fixed sample

5,000 synthetic records (2,500 archival-memory-shaped, 2,500 research-doc-shaped),
generated deterministically by `build_sample.py` with `random.seed(1568)`, drawn
from a fixed 40-tag vocabulary, 1-4 tags/record. `minerva.db` is never copied to
the pilot node (data-handling rule above) and no fleet extract was available in
this worker's sandboxed filesystem (minerva_memory.py / lumen-research-index
source were not reachable from the pilot worktree's allowed directories), so the
sample is synthetic-but-representative rather than a real extract. This is a
deviation from the card's data-handling ideal and is called out in the report.
Sample is pinned by `sample.json` + its sha256, written once by `build_sample.py`
and reused unmodified by both arms.

## Baseline (measure FIRST)

Today's split-schema pattern, reproduced structurally (two normalized SQLite
tables, one per kind, each with an indexed tag-junction table, mirroring
minerva_memory.py's per-kind archival storage and lumen-research-index's
document-metadata table): query each table's indexed tag join separately, then
merge-sort the two Python-side result lists by recency.

```
python3 run_pilot.py --arm baseline --sample sample.json --queries 200 --out results.json
```

## Treatment

Single SQLite table, one row per record regardless of kind, tags stored as a
JSON array column (`json_extract`, no index — the smallest build that matches
the proposal's "SQLite + JSON columns" shape): one SQL query per lookup, no
Python-side merge.

```
python3 run_pilot.py --arm treatment --sample sample.json --queries 200 --out results.json
```

## Kill criteria

<!-- When to stop early WITHOUT asking permission. Stopping on a kill criterion is a
successful pilot whose answer is SKIP or WATCH, not a failed one. -->

- Wall-clock exceeds 90 min -> stop, report what was measured.
- Baseline does not reproduce -> stop, report the baseline defect. Do not proceed.
- Treatment incomplete at budget -> report partial numbers, RECOMMENDATION: WATCH.

## Data handling

`/home/lumen/minerva` is not a broker read root (credentials are mirrored into its
context sections) and `minerva.db` is never copied to a pilot node. A pilot that
needs fleet data takes a purpose-built extract of only the columns it needs, written
on Lumen, and the extract is listed here.

## Scope limit (standing, not per-pilot)

A pilot NEVER wires anything into prod. An ADOPT verdict feeds minerva-factory PLAN
like any other accept and is built through the normal path, with its own review.

## Proposal — current state

Archival memory is stored in minerva.db (minerva_memory.py) and research documents are embedded into pgvector via the lumen-research-index service.

## Proposal — proposed change

Create a new lightweight agent‑centric store (SQLite + JSON columns) accessed through a new systemd service `minerva-agent-store.service` (code in /home/lumen/minerva-agent-store.py) and update minerva_memory.py and lumen-research-index to read/write via this store.

---

The report at `/home/lumen/foreman/pilots/7c516dfb1a14/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
