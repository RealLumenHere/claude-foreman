# PILOT REPORT 0eed2f5bdb92 — Writable Knowledge-Graph Memory vs pgvector+rerank

Ticket wq#1560. Run 2026-09-09. Card: `CARD.md` (same directory).
Question the defer asked: does a writable graph memory meaningfully improve
retrieval over the existing pgvector+rerank pipeline?

**It does not, at Lumen's current scale. The fusion arm is statistically
indistinguishable from the baseline and the graph-only arm is significantly worse.**

## What was measured

Task: proposal dedup. For each of the 50 proposals the live gate suppressed as a
duplicate, retrieve the parent it was suppressed against out of the 605-row dedup
index. Labels were written by production at suppression time, not by this pilot.

Fixed sample, both arms: `sample.json`,
sha256 `74f2a9bd6a6cade7fd34362b96502e94a2800672f2d59ade64dc986c5bb5a3c2`
(50 labeled queries, 605 index rows, 768-dim nomic-embed-text vectors,
`threshold=0.85` / `entity_share=0.6` copied from live `minerva_dedup`).

| arm | R@1 | R@5 | MRR | ms/query |
|---|---|---|---|---|
| **A** baseline — dense cosine + entity-share rerank (what Lumen does today) | **0.94** | 0.98 | 0.957 | 93.0 |
| **B** treatment — CGM-Lite graph only (SQLite adjacency, IDF-weighted) | 0.82 | 0.96 | 0.872 | 0.2 |
| **C** treatment — equal-weight fusion of A and B (*the arm the proposal asks for*) | 0.92 | 0.98 | 0.947 | 93.2 |

Paired exact McNemar over the per-query rank-1 hit vectors (`significance.py`):

- **A vs C: 2 queries A-only, 1 query C-only, p = 1.0000.** The fusion arm's 0.02
  R@1 deficit is one query. There is no signal here in either direction.
- **A vs B: 6 queries A-only, 0 queries B-only, p = 0.0312.** The graph alone is
  significantly worse at rank 1, not merely numerically worse.

The pre-registered win condition — treatment R@1 above baseline *and* McNemar
p < 0.05 — fails on both clauses for arm C. It fails in the baseline's favour for
arm B.

## Why the graph adds nothing here

Lumen's existing path already contains an entity-overlap stage. `run_pilot.py:151`
gates every dense candidate on `entity_share(query, title) >= 0.6`, and the graph
arm scores with the same `entities()` tokenizer (`run_pilot.py:80`). CGM-Lite's
semantic-triple retrieval therefore collapses onto a signal the production
pipeline is already applying — IDF-weighted and writable rather than a flat
threshold, but the same underlying evidence. Fusing a signal with a variant of
itself moves one query.

One labeled pair is unretrievable by both arms (rank `None` in A and B), so the
ceiling on this sample is 0.98, not 1.0. The baseline is already 0.94 of that.

## Post-hoc observation, NOT part of the verdict

Graph-only recall@k: **@5 = 0.96, @10 = 0.98, @20 = 0.98** — at 0.2 ms/query
against the baseline's 93 ms. Graph-only recall@10 reaches the same 0.98 ceiling
the baseline needs its full dense pass to reach, for ~400x less compute. That is a
candidate-generator/prefilter claim, not the retrieval-quality claim this pilot
pre-registered, and it was noticed after the primary numbers were in. It is
recorded as an open question, not as a result.

It is also unmotivated today: 93 ms over 605 rows is nobody's bottleneck. The
finding becomes worth re-measuring if the dedup index grows past roughly 10k rows
or dense retrieval shows up as a latency problem — that is the re-open trigger.

## Scope this pilot did NOT cover

The proposal has a second limb: episodic lesson memory feeding adaptive prompting
and self-improvement. This pilot tested the retrieval limb only, on one task. The
adaptation limb is untested, and nothing measured here supports or refutes it.
It is also the limb with no measured gap to close, which is why it was not built.

## Honest provenance notes

- **The card was generated with its Hypothesis / Primary metric / Fixed sample /
  Baseline / Treatment sections blank** (`minerva_defer_review` emitted the raw
  template). Those sections were written by this runner before the scored run,
  under the card's own "edit it before the run" clause.
- **`run_pilot.py`, `build_sample.py` and a `results.json` already existed in this
  directory** (01:40–01:42, written before wq#1560 was claimed at 01:43). So the
  metric was declared with a prior run's numbers already visible on disk. That
  weakens pre-registration and is stated here rather than hidden: the win condition
  chosen (paired McNemar p < 0.05) is a fixed statistical standard, not a threshold
  tuned to the observed gap, and it would have rejected arm C at any delta of this
  size. The prior file is preserved as `results.prior-0142.json`.
- **The scored run happened on Lumen, not hathor as the card specifies.**
  `fleet-exec run hathor` denied with `grant:grant_missing_env` — the pilot lane
  creates its tickets with an empty `needs:` field, so `resolve_exec_grant` issued
  no ssh grant (audit: `foreman/events/fleet-exec.jsonl`, ticket 1560). Both arms
  ran on the same host against the same file, so the quality comparison — the
  primary metric — is unaffected. Only the absolute ms/query figures are
  host-specific; the A/B/C ordering held across two runs on different load
  (this run 93.0/0.2/93.2 ms, the 01:42 run 141.1/0.3/173.7 ms) while every
  quality number reproduced to the digit.
- `significance.py` rebuilds the arms independently and asserts its aggregates
  equal `results.json` before reporting any p-value; that guard passed.

## Reproduce

```
python3 /home/lumen/foreman/pilots/0eed2f5bdb92/run_pilot.py
python3 /home/lumen/foreman/pilots/0eed2f5bdb92/significance.py
```

## Verdict

The defer asked whether graph memory would meaningfully beat pgvector+rerank. On
Lumen's own labeled dedup history it does not: fused R@1 0.92 vs baseline 0.94 at
p = 1.0, graph-only 0.82 at p = 0.0312 against it, and the mechanism is redundancy
with an entity-overlap stage the production pipeline already runs. Building
CGM-Lite would spend M-risk effort to move zero queries.

RECOMMENDATION: SKIP — baseline R@1 0.94 / R@5 0.98 / MRR 0.957 @ 93.0 ms/q; graph-only R@1 0.82 / R@5 0.96 / MRR 0.872 @ 0.2 ms/q (McNemar vs baseline p=0.0312, baseline better); fused R@1 0.92 / R@5 0.98 / MRR 0.947 @ 93.2 ms/q (McNemar vs baseline p=1.0000, no difference); n=50 labeled pairs, 605-row index. Re-open trigger: dedup index >10k rows or dense retrieval becomes a latency bottleneck, at which point graph-only recall@10=0.98 at 0.2 ms/q is worth re-measuring as a prefilter.
