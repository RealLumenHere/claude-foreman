# PILOT 5c1c8b46683a — Evaluate and optionally adopt Nemotron-3-Embed for research index

Experiment card. Generated 2026-09-12T09:10:35+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `5c1c8b46683a` |
| Deferred | 2026-08-08 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/5c1c8b46683a/REPORT.md` |

## Why this is a pilot and not a research prompt

Nemotron-3-Embed availability on Lumen's CPU-only or nephthys hardware is unconfirmed, and without an eval harness we cannot verify a retrieval improvement before rewiring a shared pgvector index. Run a feasibility trial and build a minimal retrieval eval first.

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

Lumen currently uses an embedding provider (likely nomic-embed on nephthys, exposed as nephthys-embed via LiteLLM) to embed the research corpus into pgvector through the lumen-research-index timer. Retrieval is further refined by the lumen-rerank service (local cross-encoder). No eval harness exists to measure retrieval quality changes.

## Proposal — proposed change

Add Nemotron-3-Embed to LiteLLM (config edit) and modify the lumen-research-index timer script to use it instead of the current embedding provider, then re-embed the entire research corpus into pgvector to ensure vector compatibility.

---

The report at `/home/lumen/foreman/pilots/5c1c8b46683a/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
