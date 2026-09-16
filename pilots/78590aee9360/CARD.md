# PILOT 78590aee9360 — Integrate logical-inference benchmark into Lumen model evaluation

Experiment card. Generated 2026-09-13T09:13:13+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `78590aee9360` |
| Deferred | 2026-08-08 |
| Impact / effort / risk | M / M / S |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/78590aee9360/REPORT.md` |

## Why this is a pilot and not a research prompt

The benchmark is narrowly scoped and Lumen lacks a general eval harness; adopting it now would be premature. Better to first build a reusable evaluation framework, then incorporate this and other benchmarks into it.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

Neither the proposal text nor minerva.db's fields for `78590aee9360` name an
arXiv id, paper, or dataset URL for "the arXiv benchmark" — verified by
grepping this pilot's own files and the proposal record; there is nothing
pinnable to download. This pilot substitutes a small hand-authored fixed
sample of the same item type (propositional-logic validity questions,
True/False), disclosed here rather than fabricated as a citation to a
benchmark that was never named.

Falsifiable hypothesis: a fixed logical-inference sample produces a
pass-rate spread across Lumen's currently-routed LiteLLM models wide enough
(>= 20 percentage points between the best and worst model) to inform
model-selection/routing decisions — i.e. the benchmark carries a signal not
already visible from existing health checks. If the spread is below that,
the benchmark (at this difficulty) does not distinguish models Lumen
actually routes to, and building a dedicated harness for it now is
premature — which is exactly the deferral reasoning this pilot exists to
test empirically.

## Primary metric

**Pass-rate spread** = max(per-model pass-rate) - min(per-model pass-rate)
across the configured models, on the fixed 20-question sample. Unit:
percentage points (0-100).

**Win condition, fixed before the scored run:** spread >= 20 points ->
signal exists, worth building a dedicated harness (ADOPT/WATCH territory).
Spread < 20 points -> no distinguishing signal at this difficulty (SKIP or
WATCH-pending-harder-sample). Secondary (reported, not decisive): per-model
pass rate, parse rate, wall-clock per model.

## Fixed sample

`/home/lumen/foreman/pilots/78590aee9360/sample.json` (also present in the
wq1903 branch worktree at the same relative path)
sha256 `34b95ac6fe5ba63277e198dd05a0fa684c89e7e972fbd5c8e2012416e8421900`

20 hand-authored True/False propositional-logic items (modus ponens/tollens,
affirming-the-consequent, disjunctive/hypothetical syllogism, De Morgan,
transitivity, quantifier scope, biconditionals — see `build_sample.py` for
the full list and answer key). Deterministic, no minerva.db draw, no
credentials — satisfies the Data handling note below trivially.

## Baseline (measure FIRST)

Arm A = what Lumen does today: no script in the repo computes a
logical-inference pass rate. Confirmed by exhaustive grep, not assumption:

```
grep -rn "logical" --include=*.py --include=*.yaml --include=*.md . | grep -v pilots/78590aee9360
# -> no hit is a pass-rate producer (lumen-evaluator is retired per
#    docs/fleet/retired_services.yaml; lumen-model-watchdog checks endpoint
#    health, not benchmark correctness)
```
Baseline pass-rate spread: **undefined / 0** (nothing is measured today).

## Treatment

Arm B = the smallest version of the proposal's `eval_logical_inference.py`:
reuses `prompt-forge/llm.py`'s env-first/key-file-fallback LiteLLM client
pattern, runs the fixed sample against a configurable model list, scores
True/False, writes `results.json`.

```
python3 pilots/78590aee9360/build_sample.py
python3 pilots/78590aee9360/eval_logical_inference.py \
  --models glm-5.3-flash deepseek-v4-flash qwen36-local
```

Ran on Lumen, not hathor: `fleet-exec run hathor` denied with
`grant:grant_missing_env` (ticket's `needs:` field is empty — same defect
already logged as ISSUE-LOG #257 / DEFER_PILOT_LANE.md §3.1 for wq#1560,
not fixed for wq#1903's ticket). Both arms ran on the same host against the
same fixed sample, so the comparison (pass-rate spread across models) is
unaffected; only absolute wall-clock is host-specific, and wall-clock is
secondary here, not the primary metric.

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

Lumen has no experiment/eval harness to measure model competence (identified gap). Existing services like lumen-model-watchdog check endpoint health, and lumen-evaluator scores agent outputs, but neither runs standardized benchmarks against LiteLLM-routed models.

## Proposal — proposed change

Add a standalone script (e.g. /data/lumen/scripts/eval_logical_inference.py) that runs the arXiv benchmark's questions against a configurable set of LiteLLM models, outputs pass-rate metrics to a timestamped JSON report, and optionally a systemd timer (lumen-eval-logical.timer) for periodic runs.

---

The report at `/home/lumen/foreman/pilots/78590aee9360/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
