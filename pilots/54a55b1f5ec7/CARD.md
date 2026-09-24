# PILOT 54a55b1f5ec7 — Integrate LLM-driven grey-box calibration for research pipeline simulation

Experiment card. Generated 2026-09-21T09:11:35+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `54a55b1f5ec7` |
| Deferred | 2026-08-12 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/54a55b1f5ec7/REPORT.md` |

## Why this is a pilot and not a research prompt

The proposal is promising but the current gaps (shallow intel pre-scoring, noisy research-to-action conversion) are not directly addressed by simulation calibration; existing eval harnesses already measure pipeline changes, and adding a simulation layer is a significant effort with uncertain immediate benefit.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

A simulation of the proposal -> build -> eval pipeline, calibrated by an LLM against historical outcomes, predicts proposal outcomes better than the current direct scoring.

## Primary metric

Rank correlation or AUC of predicted vs actual outcome on held-out proposals; a win needs a labelled outcome set large enough to hold a test split out.

## Fixed sample

Proposals in minerva.db with a deployed outcome. On file: 12 deployed or shipped rows out of 1687.

## Baseline (measure FIRST)

Direct scoring as it runs today (impact, effort, risk, eval_result on each proposals row). No script exists to reproduce a baseline metric against outcomes.

```
sqlite3 /home/lumen/minerva/minerva.db "select status,count(*) from proposals group by status"
```

## Treatment

calibrate_sim.py as the proposal describes it. Not built: nothing to calibrate against.

```
python3 calibrate_sim.py   # NOT BUILT
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

Lumen does not currently have a simulation of its research pipelines; the Minerva self-improvement layer (minerva-proposal, minerva-factory) evaluates proposals directly without a simulation-based calibration step.

## Proposal — proposed change

Add a new script (e.g., /data/lumen/minerva/calibrate_sim.py) that uses an LLM (via LiteLLM) to calibrate a grey-box simulation of the research pipeline (proposal -> build -> eval) against historical outcomes, and integrate it as a new timer-driven systemd unit (lumen-sim-calibrate.timer) running periodically.

---

The report at `/home/lumen/foreman/pilots/54a55b1f5ec7/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
