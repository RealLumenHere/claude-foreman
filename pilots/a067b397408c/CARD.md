# PILOT a067b397408c — Add OrchNAS orchestration layer for constrained local model search

Experiment card. Generated 2026-09-24T12:36:47+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `a067b397408c` |
| Deferred | 2026-08-09 |
| Impact / effort / risk | M / XL / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/a067b397408c/REPORT.md` |

## Why this is a pilot and not a research prompt

Deferred for a measurement; pilot wq#1934, card /home/lumen/foreman/pilots/a067b397408c/CARD.md. Original: Lumen has no dedicated inference GPU and no experiment/eval harness, so OrchNAS would require a major new subsystem with uncertain payoff. Defer until an eval harness exists and an accelerator node is available, then revisit as a scoped pilot.

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

Lumen currently selects models statically through the LiteLLM gateway (localhost:4000) and has no automated architecture search. Local inference runs on CPU-only hosts (e.g., Xeon E5-2699 v4) with manually chosen small models like qwen36-local and gemma4-e4b-local; no eval harness exists to measure whether a model change actually improves outcomes.

## Proposal — proposed change

Create a new sandboxed systemd service `lumen-orchnas` that runs OrchNAS in an isolated container on /scratch, searches over a limited architecture space for one local model (e.g., lumen-lfm2-1b), evaluates candidates by routing them through the existing LiteLLM proxy, and emits a proposal for Minerva. No production routing is touched and no model is auto-deployed.

---

The report at `/home/lumen/foreman/pilots/a067b397408c/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
