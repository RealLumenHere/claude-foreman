# PILOT 2cfb144423c8 — Implement PI-Mem parallel-iterative memory module for long research documents

Experiment card. Generated 2026-09-15T09:15:14+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `2cfb144423c8` |
| Deferred | 2026-08-09 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/2cfb144423c8/REPORT.md` |

## Why this is a pilot and not a research prompt

No experiment/eval harness exists to measure whether the technique actually improves Lumen's long-context reasoning, and the Memory v2 system is still in active iteration (P3). The paper's method is unproven for Lumen's specific workload and scale.

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

Lumen's archival memory relies on lumen-dream and lumen-reflector (Memory v2 P3), with research embeddings stored in pgvector via the lumen-research-index timer service. There is no explicit long-context iterative memory component.

## Proposal — proposed change

Create a new Python service (e.g., lumen-pi-mem) that implements the parallel-iterative memory algorithm from the PI-Mem paper. The service would process documents exceeding a configurable token threshold (e.g., >32k tokens) by iteratively compressing intermediate representations and storing them in pgvector, augmenting the existing research-index pipeline. It would be triggered by a systemd timer and run in a dry-run sandbox initially.

---

The report at `/home/lumen/foreman/pilots/2cfb144423c8/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
