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

A conformal cascade (answer with local `lumen-lfm2-1b` first, escalate to
`deepseek-v4-flash` only when a calibrated nonconformity score is exceeded) cuts
strong-model calls by at least 30% vs. always calling the strong model, while
losing no more than 3 accuracy points on TriviaQA.

## Primary metric

Accuracy (exact/alias match, fraction correct) at a fixed strong-model call
budget. Win = treatment accuracy within 3 points of baseline AND strong-model
call fraction <= 0.70.

## Fixed sample

200-row TriviaQA rc.nocontext slice reused from pilot 67af7c7e9ecf
(`pilots/67af7c7e9ecf/data/sample.jsonl`, sha256
005ee24de2177a10ad988744b532184c87a6ff4972ced4e9836fa8184a2ac3bf), split
deterministically: rows 1-40 -> `data/calib.jsonl` (threshold calibration,
sha256 cd70aed100248cbcfd45bcdeb0bf960564722afc3530bde744e1eb1a0bac8a05), rows
41-200 -> `data/sample.jsonl` (scored, sha256
9f0e5d1336251e3581b0979c32d96b7a263dcbecbf8f49451a43c11e1dbfd1d2). Both arms
score the same `data/sample.jsonl`. Run capped at the first 15 scored rows
(`PILOT_LIMIT` in `run.py`) once the weak model's calibration failure (see
REPORT.md) made a full 160-row run moot.

## Baseline (measure FIRST)

```
python3 pilots/992fcdd0fdfc/run.py baseline
```
Every row answered by `deepseek-v4-flash` alone (today's fixed-assignment
behavior) -> `data/baseline.jsonl`.

## Treatment

```
python3 pilots/992fcdd0fdfc/run.py treatment
```
`conformal_cascade.calibrate()` computes a nonconformity threshold from
`data/calib.jsonl`, then `conformal_cascade.route()` answers each row with
`lumen-lfm2-1b` first and escalates to `deepseek-v4-flash` when the score
exceeds threshold -> `data/treatment.jsonl`.

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
