# PILOT 992fcdd0fdfc — Implement Conformal Cascade Layer for Cost-Effective LLM Routing

Experiment card. Generated 2026-09-11T09:11:15+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `992fcdd0fdfc` |
| Deferred | 2026-07-30 |
| Impact / effort / risk | M / M / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/992fcdd0fdfc/REPORT.md` |

## Why this is a pilot and not a research prompt

Promising in principle, but requires a calibration phase with representative queries, an eval harness to measure quality/cost trade-offs, and a Lumen-specific integration into LiteLLM’s routing. Defer until a controlled pilot can be designed and a baseline cost/accuracy dataset exists.

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

Lumen routes all inference through LiteLLM (localhost:4000) with fixed model assignments per service; no dynamic confidence-based fallback between local and remote models exists. Local models (e.g., gemma4-e4b-local, qwen36-local, lumen-lfm2-1b) are used only when explicitly configured, not as a first-pass cascade.

## Proposal — proposed change

Add a new lightweight Python service (conformal_cascade.py + lumen-cascade systemd unit) that intercepts LLM requests, runs them through a calibrated local model (e.g., lumen-lfm2-1b), computes a conformal prediction set using a pre-calibrated threshold, and only forwards to the requested remote model if the local prediction is not sufficiently confident. Integrate via LiteLLM’s custom_llm_model or proxy-mode configuration.

---

The report at `/home/lumen/foreman/pilots/992fcdd0fdfc/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
