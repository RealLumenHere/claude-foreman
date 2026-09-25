# PILOT {{PID}} — {{TITLE}}

Experiment card. Generated {{GENERATED}} by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. The five spec sections below are authored by the generator, not
by the runner (wq#2690, `research/DEFER_PILOT_LANE.md` §3.1a): a runner that finds
them missing, or finds a DO-NOT-RUN banner, blocks the ticket rather than inventing
a measurement plan. Never edit the card during a run.

| | |
|---|---|
| Proposal | `{{PID}}` |
| Deferred | {{DEFERRED_ON}} |
| Impact / effort / risk | {{IMPACT}} / {{EFFORT}} / {{RISK}} |
| Node | {{NODE}} |
| Wall-clock budget | {{WALLCLOCK_MIN}} min |
| Token budget | {{TOKEN_BUDGET}} |
| Report | `{{REPORT_PATH}}` |

## Why this is a pilot and not a research prompt

{{DEFER_REASON}}

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

- Wall-clock exceeds {{WALLCLOCK_MIN}} min -> stop, report what was measured.
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

{{CURRENT_STATE}}

## Proposal — proposed change

{{PROPOSED_CHANGE}}

---

The report at `{{REPORT_PATH}}` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
