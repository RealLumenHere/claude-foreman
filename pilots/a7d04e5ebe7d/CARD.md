# PILOT a7d04e5ebe7d — Implement Complementary Memory Traces for Multi-hop Retrieval

Experiment card. This card did not exist when wq#1684 (attempt 2) was
dispatched — `minerva_defer_review` (wq#1559) never wrote
`pilots/a7d04e5ebe7d/CARD.md` at all, a stricter instance of the card-
generator defect logged as `kb/ISSUE-LOG.md` issue #257 (there the card was
at least generated, just empty). Filled in here under #257's own precedent
("edit it before the run if it is wrong; never edit it during") using only
the ticket's own detail text — `minerva.db` is not a readable root for a
pilot worker (`foreman/research/DEFER_PILOT_LANE.md` §"Data that must not
cross to a pilot node"), so the proposal's original `current_state` /
`proposed_change` prose could not be recovered and is not reproduced below.

| | |
|---|---|
| Proposal | `a7d04e5ebe7d` |
| Ticket | wq#1684 |
| Impact / effort / risk | unknown (proposal row not recoverable, see above) / M (assigned) / L |
| Node | Lumen (see node substitution below) |
| Wall-clock budget | 60 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/a7d04e5ebe7d/REPORT.md` |

## Node substitution (disclosed, precedent: ISSUE-LOG #257 and #33)

Ticket says "Run on hathor." `pilots/2c11123fab8b/REPORT.md` (wq#1682)
already established that hathor has been physically powered off since
2026-07-18 pending Frank-at-the-bench work (`kb/ISSUE-LOG.md` issue #33) —
not something a headless BUILD worker can do or should block on under the
full-autonomy standing order. Both arms run on Lumen instead, against the
identical fixed sample, following the same precedent wq#1560 set
(`kb/ISSUE-LOG.md` issue #257). This pilot has no wall-clock-sensitive
metric (pure CPU, stdlib only), so the substitution does not affect the
comparison the way it might for a latency pilot.

## Why this is a pilot and not a research prompt

Lumen lacks an evaluation harness to measure retrieval quality improvement;
without that, adopting a novel technique is premature and risky. The paper
is also unverified outside arXiv. Defer until an eval harness exists or
empirical benefit is demonstrated elsewhere.

The harness did not exist, so building the smallest honest one is in scope
for this pilot (`run_pilot.py` + `build_sample.py`, both in this directory,
stdlib only — same posture as `memory/recall.py`).

## Hypothesis

A retriever that keeps a second, complementary "entity trace" alongside
each fact's verbatim trace, and runs a second retrieval hop seeded by the
entities the first hop surfaced, answers multi-hop questions — where the
second supporting fact shares no words with the original query, only an
entity the first fact names — at a recall@bridge at least 20 percentage
points higher than a single-verbatim-trace retriever, on a fixed sample.

## Primary metric

`recall_at_bridge`: fraction of fixed multi-hop questions for which BOTH
gold-labeled supporting facts appear in the top-k (k=4) retrieved set.
Win, decided before running: treatment `recall_at_bridge` - baseline
`recall_at_bridge` >= +0.20 -> mechanism confirmed; 0 < delta < 0.20 ->
mechanism weak; delta <= 0 -> mechanism does not help this sample.

## Fixed sample

`pilots/a7d04e5ebe7d/sample.json`, built deterministically by
`build_sample.py` (no randomness) — 41 facts (8 two-hop bridge pairs + 24
single-hop distractors sharing no entity with any bridge pair) and 8
multi-hop questions, each requiring both facts of one bridge pair. Facts
are grounded in real statements from this repo's own docs/ISSUE-LOG (node
IPs, ticket numbers, prior pilot outcomes) rather than arbitrary text, so
every gold label is hand-checkable against a citable source. Pinned by
sha256, recorded by `run_pilot.py` at run time and in REPORT.md.

Both arms see this exact file; neither is regenerated between runs.

## Baseline (measure FIRST)

`memory/cmt_retrieval.retrieve_single_trace` — ranks facts by token overlap
between the query and each fact's own verbatim text; top-k, k=4. This is
the ceiling of what a single verbatim trace can do; `recall.py` today does
not even do this (it injects top-k-newest with no query at all), so this
baseline is already a step up from production, established first so the
treatment cannot be tuned to beat a weaker baseline than actually exists.

```
cd pilots/a7d04e5ebe7d && python3 run_pilot.py
```

## Treatment

`memory/cmt_retrieval.retrieve_complementary_traces` — hop 1 is the
baseline retriever (top hop1_k=2); hop 2 extracts entity-shaped tokens
(IPv4s, CamelCase/ALLCAPS identifiers, `wq#N` ids, file-ish paths, `flag N`)
from hop 1's own results and re-scores every other fact by entity overlap
with that bridge set, appending matches until k=4 is filled. Smallest build
that tests the hypothesis: no embeddings, no external service, no change to
`recall.py`'s existing behavior (this ships as a new, separate module,
`memory/cmt_retrieval.py`, not wired into the live recall path — a pilot
never wires into prod).

```
cd pilots/a7d04e5ebe7d && python3 run_pilot.py
```

## Kill criteria

- Wall-clock exceeds 60 min -> stop, report what was measured. (Not hit —
  full run is sub-second, stdlib only.)
- Baseline does not reproduce -> stop, report the baseline defect. Do not
  proceed. (Not hit.)
- Treatment incomplete at budget -> report partial numbers,
  RECOMMENDATION: WATCH. (Not hit — full run completed.)

## Data handling

No fleet data used. `sample.json` is synthetic, built by `build_sample.py`
from facts written into this card and this repo's own docs/ISSUE-LOG, no
`minerva.db` read, no credentials, no `.env`.

## Scope limit (standing, not per-pilot)

A pilot NEVER wires anything into prod. An ADOPT verdict feeds
minerva-factory PLAN like any other accept and is built through the normal
path, with its own review. `memory/cmt_retrieval.py` is additive and
inert — nothing imports it outside this pilot directory.

## Proposal — current state / proposed change

Not recoverable from `minerva.db` (see header); the ticket's own summary is
the only surviving spec: "Implement Complementary Memory Traces for
Multi-hop Retrieval" applied to Lumen's fact-based memory store.

---

The report at `/home/lumen/foreman/pilots/a7d04e5ebe7d/REPORT.md` MUST end
with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once
and then retired unjudged — the line is not optional.
