# PILOT 0eed2f5bdb92 — Add Writable Knowledge-Graph Memory to Complement pgvector

Experiment card. Generated 2026-09-09T01:40:02+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `0eed2f5bdb92` |
| Deferred | 2026-09-08 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/0eed2f5bdb92/REPORT.md` |

## Why this is a pilot and not a research prompt

The paper's approach is promising but requires adaptation to Lumen's specific memory architecture; a concrete open question is whether the graph memory will meaningfully improve retrieval/adaptation over the existing pgvector+rerank pipeline. A small-scale pilot on a specific task (e.g., proposal dedup or research recall) is needed before full adoption.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

A writable knowledge-graph memory (CGM-Lite: entity adjacency over proposals),
fused with the existing dense+rerank path, retrieves the correct duplicate parent
at rank 1 more often than dense+rerank alone on Lumen's own labeled dedup history.

Falsifier: if fused R@1 is not above baseline R@1 by a margin that survives a paired
significance test, the graph does not meaningfully improve retrieval and the answer
is SKIP or WATCH, not ADOPT.

## Primary metric

**R@1 on proposal dedup** — the fraction of the 50 labeled queries whose true
parent_id is ranked first out of the 605-row dedup index. Unit: proportion, 0–1.

**Win condition, fixed before the run:** treatment R@1 > baseline R@1 AND the
difference survives an exact paired McNemar test at p < 0.05 over the per-query
hit/miss vectors. A raw delta without that test is noise at n=50 (one query = 0.02),
so a delta alone does not count as a win.

Secondary (reported, not decisive): R@5, MRR, ms/query.

## Fixed sample

`/home/lumen/foreman/pilots/0eed2f5bdb92/sample.json`
sha256 `74f2a9bd6a6cade7fd34362b96502e94a2800672f2d59ade64dc986c5bb5a3c2`

- 50 query rows: every proposal the LIVE dedup gate suppressed (`status='duplicate'`
  with a non-empty `parent_id`) whose parent is present in the dedup index. The
  parent_id label was written by production at suppression time, not by this pilot.
- 605 index rows: `proposal_dedup` rows with a stored vector.
- 768-dim `nomic-embed-text` vectors for both sides; `threshold=0.85`,
  `entity_share=0.6` copied from `minerva_dedup` so the baseline is the live
  configuration, not a re-tuned one.

Extract built by `build_sample.py` on Lumen (only the columns named above; no
credentials, no `minerva.db` copy). Both arms read this one file.

## Baseline (measure FIRST)

Arm A = what Lumen does today: cosine over the stored vectors, then the entity-share
gate as the rerank stage, exactly as `minerva_dedup.check` applies it.

```
python3 /home/lumen/foreman/pilots/0eed2f5bdb92/run_pilot.py
# arm A is scored first, before the graph is consulted for any ranking
```

## Treatment

Arm B = CGM-Lite reduced to the smallest thing that can answer the question: a
writable SQLite adjacency table of (proposal -> entity) edges, IDF-weighted, scored
by weighted shared-entity overlap. Arm C = equal-weight fusion of A and B, which is
the arm the hypothesis is about — the proposal says *complement* pgvector, not
replace it.

```
python3 /home/lumen/foreman/pilots/0eed2f5bdb92/run_pilot.py    # arms A, B, C
python3 /home/lumen/foreman/pilots/0eed2f5bdb92/significance.py # paired McNemar A vs C
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

Lumen currently uses pgvector for memory retrieval (Memory V3, bi-temporal state, episodic recall via lumen_episode.py) but has no writable knowledge-graph memory; graph-like relational knowledge is not explicitly stored or updated.

## Proposal — proposed change

Implement a lightweight continual graph memory service (CGM-Lite) that stores semantic triples and episodic lessons in a writable graph (e.g., using SQLite with a simple adjacency table or a dedicated graph DB like Kuzu). Integrate with existing memory pipeline: semantic graph updated via quality-gated operations; episodic lesson memory stores recent outcomes and failure cases. Use this graph to augment retrieval for adaptive prompting and self-improvement, complementing pgvector.

---

The report at `/home/lumen/foreman/pilots/0eed2f5bdb92/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
