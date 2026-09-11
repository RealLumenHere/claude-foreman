# PILOT a7d04e5ebe7d — Implement Complementary Memory Traces for Multi-hop Retrieval

Experiment card. Generated 2026-09-10T09:14:16+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `a7d04e5ebe7d` |
| Deferred | 2026-07-30 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/a7d04e5ebe7d/REPORT.md` |

## Why this is a pilot and not a research prompt

Lumen lacks an evaluation harness to measure retrieval quality improvement; without that, adopting a novel technique is premature and risky. The paper is also unverified outside arXiv. Defer until an eval harness exists or empirical benefit is demonstrated elsewhere.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

A minimal Complementary Memory Trace (CMT-lite) — reranking hop-1 dense-retrieval
candidates using a precomputed second-hop "trace" vector (the centroid of each
candidate's own nearest neighbours) — retrieves the correct target at rank 1 more
often than single-hop dense cosine alone, on Lumen's own labeled retrieval history.

Falsifier: if treatment R@1 is not above baseline R@1 by a margin that survives a
paired significance test, multi-hop trace reranking does not meaningfully improve
retrieval here and the answer is SKIP or WATCH, not ADOPT.

## Primary metric

**R@1** — the fraction of the 50 labeled queries whose true target (`parent_id`)
is ranked first out of the 605-row index. Unit: proportion, 0-1.

**Win condition, fixed before the run:** treatment R@1 > baseline R@1 AND the
difference survives an exact paired McNemar test at p < 0.05 over the per-query
hit/miss vectors (n=50, so one query = 0.02 — a raw delta alone is noise).

Secondary (reported, not decisive): arm B hit/miss breakdown, ms/query.

## Fixed sample

Reused from pilot `0eed2f5bdb92` (same defer-pilot lane, same fixed-sample
discipline, no new fleet extract needed): `/home/lumen/foreman/pilots/0eed2f5bdb92/sample.json`
(50 labeled query rows + 605-row index, live 768-dim `nomic-embed-text` vectors,
built by that pilot's `build_sample.py` from production `proposal_dedup` rows;
no credentials, no `minerva.db` copy). This proposal has no committed retrieval
task/labels of its own — Lumen's only existing labeled retrieval-quality dataset
is this dedup set, so both arms here read that same file, unmodified.

Node substitution: CARD says "Node: hathor"; this ticket's `needs:` tags carry no
`ssh(hathor)` grant (same lane defect as ISSUE-LOG #257 / #33 — hathor has also
been powered off since 2026-07-18). Both arms run on Lumen instead, same fixed
sample, so the R@1 comparison (the primary metric) is unaffected; only absolute
ms/query is host-specific.

## Baseline (measure FIRST)

Arm A = what Lumen does today for this retrieval task: single-hop cosine
similarity over the stored embeddings, top-1 by direct score.

```
python3 /home/lumen/worktrees/wq1684-a2/pilots/a7d04e5ebe7d/run_pilot.py
# arm A (single-hop) is scored first, before any trace is consulted
```

## Treatment

Arm B = CMT-lite: precompute a trace vector per index row (centroid of its own
top-3 nearest neighbours), then rerank the top-5 hop-1 candidates by
`0.7*cosine(query,candidate) + 0.3*cosine(query,trace(candidate))`.

```
python3 /home/lumen/worktrees/wq1684-a2/pilots/a7d04e5ebe7d/run_pilot.py    # arms A, B
python3 /home/lumen/worktrees/wq1684-a2/pilots/a7d04e5ebe7d/significance.py # paired McNemar A vs B
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

Lumen's retrieval pipeline uses pgvector embeddings and a cross-encoder rerank service (lumen-rerank) for single-hop retrieval; no multi-hop memory trace mechanism exists.

## Proposal — proposed change

Introduce a new service (e.g., lumen-cmt) that implements the CMT-RAG technique, storing intermediate reasoning trace embeddings and using them to guide subsequent retrieval hops in the intel/scout pipelines. This would require a new pgvector collection for trace vectors, a new systemd unit, and modifications to lumen-intel and lumen-scout to optionally invoke multi-hop retrieval.

---

The report at `/home/lumen/foreman/pilots/a7d04e5ebe7d/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
