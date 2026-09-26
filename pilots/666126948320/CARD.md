# PILOT 666126948320 — Implement Field-Aware Tool Routing for LiteLLM Queries

Experiment card. Generated 2026-09-25T20:34:41+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. The five spec sections below are authored by the generator, not
by the runner (wq#2690, `research/DEFER_PILOT_LANE.md` §3.1a): a runner that finds
them missing, or finds a DO-NOT-RUN banner, blocks the ticket rather than inventing
a measurement plan. Never edit the card during a run.

| | |
|---|---|
| Proposal | `666126948320` |
| Deferred | 2026-08-25 |
| Impact / effort / risk | M / M / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/666126948320/REPORT.md` |

## Why this is a pilot and not a research prompt

Deferred for a measurement; pilot wq#2654, card /home/lumen/foreman/pilots/666126948320/CARD.md. Original: The paper's technique is promising but requires a concrete mapping of query fields to Lumen's specific models and tasks. A pilot study is needed to validate benefits and avoid disrupting existing pipelines.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

Field-aware routing beats static per-service model selection on exact-match task success rate by at least 5 percentage points on the pinned 60-query evaluation set.

## Primary metric

Task success rate in percent (fraction of pinned queries whose LiteLLM response is exact-match correct per grader); win declared if treatment >= baseline + 5.0 points.

## Fixed sample

sha256 of /home/lumen/foreman/pilots/666126948320/queries.jsonl = 0000000000000000000000000000000000000000000000000000000000000000 (60 queries, one per line, keys: query, task_type, modality, gold); both arms run all 60.

## Baseline (measure FIRST)

```
/home/lumen/foreman/pilots/666126948320/run_static.sh --queries /home/lumen/foreman/pilots/666126948320/queries.jsonl --out /home/lumen/foreman/pilots/666126948320/baseline.jsonl
```

## Treatment

```
/home/lumen/foreman/pilots/666126948320/run_routed.sh --queries /home/lumen/foreman/pilots/666126948320/queries.jsonl --routes /home/lumen/foreman/pilots/666126948320/routes.yaml --out /home/lumen/foreman/pilots/666126948320/treatment.jsonl
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

Lumen currently routes all inference through LiteLLM (localhost:4000) with a static model selection per service, e.g., lumen-fleet-* services use fixed models (cerebras, mistral, openrouter). No dynamic field-aware routing exists.

## Proposal — proposed change

Add a lightweight routing layer (Python script or config) that inspects query fields (e.g., task type, modality, required context) and selects the most appropriate LiteLLM model/provider. Integrate as a pre-processing step in the LiteLLM gateway or as a wrapper for existing services.

---

The report at `/home/lumen/foreman/pilots/666126948320/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
