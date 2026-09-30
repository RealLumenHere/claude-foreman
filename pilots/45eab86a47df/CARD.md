# PILOT 45eab86a47df — Add DE-NER dialogue-elicitation prompt skill to signal engine for zero-shot NER before pgvector indexing

Experiment card. Generated 2026-09-22T09:12:19+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `45eab86a47df` |
| Deferred | 2026-08-14 |
| Impact / effort / risk | M / M / S |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/45eab86a47df/REPORT.md` |

## Why this is a pilot and not a research prompt

The technique is promising but unproven in Lumen's context; existing entity familiarity pipeline already covers some needs. Defer until a small-scale experiment validates benefit over current approach.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

DE-NER two-turn dialogue elicitation beats the deployed regex extractor
(`familiarity_scorer.py extract()`) by at least 0.15 micro-F1 on entity-set match
against a fixed hand-labeled sample.

## Primary metric

Micro-F1 (entity-set match, normalized lowercase, per-item precision/recall summed
across the sample) on the fixed sample below. Win = treatment F1 >= baseline F1 + 0.15,
set before either arm was run.

## Fixed sample

`sample.json` in this directory: 6 hand-authored title+reasoning pairs shaped like
`classified_intel` rows, 19 gold entities total (pinned fixture, `minerva.db` not
copied off Lumen per CARD Data handling — see file header). Both arms run against the
same 6 items.

## Baseline (measure FIRST)

```
python3 baseline_extract.py sample.json
```

Reimplements the deployed `familiarity_scorer.py extract()` regexes verbatim (see
`baseline_extract.py` header) — the current entity-familiarity approach referenced in
the defer rationale.

## Treatment

```
python3 de_ner_skill.py sample.json
```

Two-turn DE-NER prompt (candidate listing, then self-verification pass explicitly
covering lowercase/hyphenated names) — see `de_ner_skill.py` for the prompt text and
the run note on how turn 2 was executed in this sandbox.

Both scored together via `python3 run_pilot.py` -> `results.json`.

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

Lumen's signal engine (lumen-signal-engine.timer) ingests real-time content but does not perform explicit named entity extraction before indexing into pgvector; entity familiarity is handled separately by lumen-familiarity.timer from classified_intel.

## Proposal — proposed change

Implement a new prompt skill in the signal engine that uses dialogue elicitation (per DE-NER) to extract named entities from research papers and web content, storing results in a new column or side table before pgvector indexing. This involves adding a prompt template and a post-processing step in the signal engine's pipeline.

---

The report at `/home/lumen/foreman/pilots/45eab86a47df/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
