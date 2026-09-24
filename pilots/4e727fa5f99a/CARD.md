# PILOT 4e727fa5f99a — Integrate MamaBench counterfactual test suite into Lumen model evaluation

Experiment card. Generated 2026-09-20T09:14:04+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `4e727fa5f99a` |
| Deferred | 2026-08-12 |
| Impact / effort / risk | M / M / S |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/4e727fa5f99a/REPORT.md` |

## Why this is a pilot and not a research prompt

Niche medical benchmark with limited relevance to Lumen's core homelab tasks; existing eval harnesses already cover robustness, so integration is possible but not urgent.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

MamaBench's Bias Trap Rate separates two local models that pelican_bench scores 0.008 apart (qwen36-local 0.955, isis-qwen3-32b 0.963) by at least 10 BTR points.

## Primary metric

BTR difference between the two models, in points; a win is >= 10 points on the pinned sample. BTR = P(model fails the counterfactual | it passed the base case).

## Fixed sample

60 of the 217 counterfactual pairs (120 narratives), chosen by fixed seed, pinned by row ids plus the sha256 of the parquet. NOT PINNABLE TODAY: the dataset (HelpMum-Personal/MamaBench, cc-by-4.0, 434 rows) is gated=auto on Hugging Face, answers 401 without a token, and Lumen has no HF token.

## Baseline (measure FIRST)

Existing scores on file: `python3 /home/lumen/minerva/pelican_bench.py --models qwen36-local,isis-qwen3-32b` (pelican_bench.db: 0.955 and 0.963, 4 fixed probes, mechanical scorers, no judge).

```
python3 /home/lumen/minerva/pelican_bench.py --models qwen36-local,isis-qwen3-32b
```

## Treatment

A runner that sends each narrative through LiteLLM (diagnosis name on the first line), scores the label by string match, and computes BTR per pair. Not built.

```
python3 mamabench_btr.py --pairs 60 --seed 2126 --models qwen36-local,isis-qwen3-32b   # NOT BUILT
```

## Kill criteria



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

Lumen has existing eval harnesses (DGM v2 paired_eval, pelican_bench, model_audit) but no medical-domain robustness benchmark.

## Proposal — proposed change

Add MamaBench as a new evaluation dataset in the pelican_bench or model_audit pipeline, running counterfactual clinical perturbation tests on local models via LiteLLM.

---

The report at `/home/lumen/foreman/pilots/4e727fa5f99a/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
