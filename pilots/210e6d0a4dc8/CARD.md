# PILOT 210e6d0a4dc8 — Integrate ForgetBench evaluation suite for long-term parametric memory monitoring

Experiment card. Generated 2026-09-24T12:47:55+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `210e6d0a4dc8` |
| Deferred | 2026-08-11 |
| Impact / effort / risk | M / M / S |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/210e6d0a4dc8/REPORT.md` |

## Why this is a pilot and not a research prompt

Deferred for a measurement; pilot wq#2078, card /home/lumen/foreman/pilots/210e6d0a4dc8/CARD.md. Original: The benchmark is academic and may not directly map to Lumen's memory architecture; defer until more concrete evidence of applicability or a specific memory retention issue arises.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

(Filled in by the runner before any scored call, per this card's "edit it before the run" clause: the card was generated blank.)

Running ForgetBench on Lumen's local model on a timer produces a retention trend that moves over time and can flag a memory problem. Precondition: the benchmark can be obtained and its forgetting mechanism applies to a frozen, served model.

## Primary metric

Not reached; both preconditions fail, see Baseline. Had they held: week-over-week change in retention score on the same fixed knowledge stream, win = a change above run-to-run noise.

## Fixed sample

None pinned. The benchmark's data is not obtainable (no released code or dataset found), and it defines its stream by sequential knowledge edits to model weights.

## Baseline (measure FIRST)

Precondition checks, run first:

```
# 1. is the code or dataset released? arXiv 2607.26455 (2026-07-29) says code follows acceptance; searches on 2026-09-24 found no repository.
# 2. does anything in Lumen edit or fine-tune local model weights?
systemctl list-unit-files | grep -i -E 'lora|finetune|fine-tune|train|model-edit'    # nothing
```

## Treatment

Not built.

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

Lumen has memory consolidation (lumen-dream, lumen-reflector) and memory integrity checks (lumen-embed-guard, lumen-memory-audit), but no dedicated benchmark for long-term parametric memory retention.

## Proposal — proposed change

Add a new timer-driven service (lumen-forgetbench) that runs the ForgetBench evaluation suite against local models (e.g., lumen-lfm2.5-2.6b) via LiteLLM, storing results in a new table in minerva.db for trend analysis.

---

The report at `/home/lumen/foreman/pilots/210e6d0a4dc8/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
