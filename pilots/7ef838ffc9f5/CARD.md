# PILOT 7ef838ffc9f5 — Integrate personalization benchmark to seed Lumen eval harness

Experiment card. Generated 2026-09-13T09:13:12+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `7ef838ffc9f5` |
| Deferred | 2026-08-08 |
| Impact / effort / risk | M / L / S |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/7ef838ffc9f5/REPORT.md` |

## Why this is a pilot and not a research prompt

The paper offers a benchmark, not an integration-ready tool; mapping its tasks onto Lumen's multi-service personalization stack and building the eval harness is a substantial project that should wait until a general eval infrastructure exists.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

(Filled in by the runner before any scored call, per this card's own "edit it
before the run" clause — the card was generated blank, same situation as
pilots 0eed2f5bdb92, 7c516dfb1a14, and 7187e39f4cbf.)

A minimal personalization-eval harness — a fixed set of tasks each pairing
injected persona/preference facts with a query that only a model using those
facts can answer well, scored by a keyword/format rubric — produces a
discriminating signal: a model given the facts (treatment, standing in for
what lumen-persona-engine + lumen-convo-learn would supply) scores at least
1.5x the same model given no facts (baseline, today's default), on the same
fixed sample. This is the prerequisite for the harness being useful as a seed
for Lumen's eval infrastructure; if treatment does not clearly beat baseline,
the rubric/sample is not measuring personalization at all.

## Primary metric

Mean per-task rubric score (0-1, `run_pilot.py:score`) across the 8-task
fixed sample, averaged per arm. Win condition, fixed before the run:
treatment mean >= 1.5 * baseline mean.

## Fixed sample

8 hand-authored personalization tasks, `sample.json` (sha256
`e7f188635798726827af594e7e2d60c49ae338eb397b10cfef9d7b34c23eed7a`), generated
deterministically by `build_sample.py` (no RNG). The source paper's own task
set could not be pulled into this sample — see "Honest provenance" in
`REPORT.md` — so this is a proxy sample covering the same shape (preference
recall, format/style adherence, prior-context memory) described in the
paper's classified-intel summary. Both arms run against the identical file.

## Baseline (measure FIRST)

Today's default: no persona/memory context injected, matching current Lumen
behavior for a cold request (persona-engine/convo-learn context not modeled;
their code is not in this worktree — see Scope below).

```
python3 run_pilot.py --arm baseline --sample sample.json --out results_baseline.json
```

## Treatment

The smallest stand-in for "persona+memory context is available to the
model": the task's fact string injected into the system prompt before the
same user query. No lumen-persona-engine or lumen-convo-learn service call —
this tests whether the harness/metric discriminates personalized from
generic responses, not the live integration (per the defer reason, that
integration is the substantial project this pilot exists to scope, not to
build).

```
python3 run_pilot.py --arm treatment --sample sample.json --out results_treatment.json
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

Lumen personalization currently relies on lumen-persona-engine (refills lp_*.txt prompts) and lumen-convo-learn (durable memory extraction), but there is no eval harness to measure personalization quality (a known gap).

## Proposal — proposed change

Create a benchmark-runner script that pulls the paper's personalization tasks, runs them against Lumen's current persona+memory pipeline through LiteLLM, and logs scores; no changes to live services.

---

The report at `/home/lumen/foreman/pilots/7ef838ffc9f5/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
