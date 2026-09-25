# PILOT 76c39dd5afde — Implement adaptive cross-task memory transfer for research pipelines

Experiment card. Generated 2026-09-23T09:11:11+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `76c39dd5afde` |
| Deferred | 2026-08-15 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/76c39dd5afde/REPORT.md` |

## Why this is a pilot and not a research prompt

The paper is research-focused and not a ready-to-use tool; the proposed algorithm's applicability to Lumen's specific pipelines is uncertain. A concrete, answerable question is whether the extracted snippets would actually improve research outcomes, which requires a pilot study. Deferring allows time to design a small-scale experiment to validate the approach before full integration.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

(Filled in by the runner (wq#2436) after a precondition probe; the card was generated blank.)

Injecting snippets mined from prior grounding-passing research notes into the live fleet
researcher's prompt raises its per-attempt grounding pass rate by at least 10 points
absolute over today's pipeline, which already injects local-corpus context.

## Primary metric

Per-attempt grounding pass rate of `lumen-fleet-*` (`grounding: pass` / all `grounding:`
lines), in percent. Win: treatment >= baseline + 10 pts on the same topics. Secondary:
addressable-failure share (grounding fails plus non-degenerate quality-gate fails over
all scored attempts). This is the ceiling on what prompt context of any kind can fix.

## Fixed sample

`pilots/76c39dd5afde/fleet_journal_2026-09-18_25.log`: journal of lumen-fleet-openrouter,
-cerebras, and -mistral, requested for 09-18..09-25. Retention held only
2026-09-23 00:20 to 2026-09-24 20:21: 2115 lines, 364 scored attempts,
sha256 `8f9604bbfdb8da54b034ad16fb133418fabf23a0d1d26724b83a9b5bbe90d391`.

## Baseline (measure FIRST)

```
python3 pilots/76c39dd5afde/measure_baseline.py pilots/76c39dd5afde/fleet_journal_2026-09-18_25.log
```

## Treatment

Not run. See REPORT.md: the researcher source (`/home/lumen/agent`) is outside the
worker sandbox, and `fleet-exec run hathor` is denied (`grant:grant_missing_env`).
A faithful arm would re-run the same topics with a snippet block prepended to the
prompt, so it needs both the pipeline code and a node to run it on.

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

Lumen's research pipelines (e.g., lumen-fleet-*, lumen-orchestrator) operate independently without sharing learned program snippets or strategies across tasks. Memory consolidation (lumen-dream) and skill creation are stalled, and there is no mechanism for cross-task transfer of successful code patterns.

## Proposal — proposed change

Create a new service (lumen-memtransfer) that periodically analyzes successful research pipeline executions (from logs and work queue) to extract reusable code snippets or strategies, stores them in a dedicated table in the existing database, and injects them as context into future research prompts via the prompt engine. This is a new service with a new table, requiring schema migration.

---

The report at `/home/lumen/foreman/pilots/76c39dd5afde/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
