# PILOT be88b11ea170 — Implement Compression-Based Sparse Attention for Local Models

Experiment card. Generated 2026-09-24T12:36:47+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `be88b11ea170` |
| Deferred | 2026-08-10 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/be88b11ea170/REPORT.md` |

## Why this is a pilot and not a research prompt

Deferred for a measurement; pilot wq#2077, card /home/lumen/foreman/pilots/be88b11ea170/CARD.md. Original: The paper is academic and not yet validated for production; implementing it would require significant effort and risk to the inference pipeline without clear evidence of benefit for Lumen's specific models and workloads. Defer until more mature implementations

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

<!-- One falsifiable sentence. "X beats Y on metric M by at least N" — not "X is
promising". If it cannot be written this way the pilot is not ready to run. -->

## Primary metric

<!-- ONE number. Name it, name its unit, name what counts as a win BEFORE running.
A pilot that picks its metric after seeing the numbers has measured nothing. -->

## Fixed sample

<!-- Exactly what the baseline and the treatment both run against. Pin it: a path,
a row count, a sha256 where the set can drift. Both arms see the same sample or the
comparison is void. -->

## Baseline (measure FIRST)

<!-- The command that measures what Lumen does today. Run this before building the
treatment, so the baseline cannot be tuned to lose. -->

```
```

## Treatment

<!-- The smallest thing that tests the hypothesis. Not the full proposal — the
cheapest build that produces the primary metric. -->

```
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

Lumen routes all inference through LiteLLM (localhost:4000) and uses local models like lumen-lfm2.5-2.6b, but there is no sparse attention mechanism in the inference pipeline; attention is standard full attention.

## Proposal — proposed change

Add a new service or library integration that applies the compression-based sparse attention algorithm to local model inference. Specifically, create a wrapper or pre-processing step that selects key tokens via compression-based content selection before attention computation, reducing memory and compute. This could be implemented as a new Python module (e.g., /data/lumen/sparse_attention/) and integrated into the LiteLLM routing for local models.

---

The report at `/home/lumen/foreman/pilots/be88b11ea170/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
