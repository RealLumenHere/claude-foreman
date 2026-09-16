# PILOT 78590aee9360 — Integrate logical-inference benchmark into Lumen model evaluation

Ran by wq#1903 (BUILD worker, worktree `wq1903-a1`), 2026-09-16.

## Deviations from CARD.md, disclosed up front

1. **No hathor.** `fleet-exec run hathor` denied every attempt with
   `grant:grant_missing_env` — the wq#1903 ticket's `needs:` field is empty,
   the same defect already logged in `/home/lumen/kb/ISSUE-LOG.md` (#257)
   for the first pilot (wq#1560) and described as unfixed-per-ticket in
   `foreman/research/DEFER_PILOT_LANE.md` §3.1. Both arms ran on Lumen
   instead. Same host for both arms, same fixed sample, so the primary
   metric (cross-model pass-rate spread) is unaffected; only absolute
   wall-clock is host-specific and is secondary here.
2. **No named benchmark to pin.** The proposal says "the arXiv benchmark's
   questions" but names no id/URL, and `78590aee9360`'s proposal record
   carries none either (verified by grep, not assumed). Substituted a
   20-item hand-authored fixed sample of the same item type
   (propositional-logic validity, True/False) — see CARD.md "Hypothesis"
   for the full disclosure and `build_sample.py` for the item list.
3. **Metric declared after one exploratory run.** While building
   `eval_logical_inference.py` a first pass used `max_tokens=10`, which cut
   off `glm-5.3-flash`'s reasoning tokens before it emitted an answer
   (`finish_reason: "length"`, `content: null` — a real, separately useful
   finding: reasoning-style models need a larger token budget for this kind
   of prompt). That run's `results.json` existed on disk before the win
   condition below was written down. Disclosed per the same class of issue
   already solved for wq#1560 (ISSUE-LOG #257): the win-condition threshold
   (20-point spread) is a round, pre-committed number, not tuned to what
   was observed, and the observed result (0-point spread, all three models
   at 100%) is far enough from any plausible threshold (10-30 points) that
   the verdict does not depend on the exact number chosen.

## Baseline (Arm A)

`grep -rn "logical" --include=*.py --include=*.yaml --include=*.md .`
(repo-wide, excluding this pilot's own files) turns up no script that
computes a logical-inference pass rate. `lumen-evaluator` is retired
(`docs/fleet/retired_services.yaml`); `lumen-model-watchdog` checks endpoint
health, not benchmark correctness. Confirms the proposal's stated gap.

**Baseline pass-rate spread: undefined (nothing measured today).**

## Treatment (Arm B)

`eval_logical_inference.py` (this branch, `pilots/78590aee9360/`), 20-item
fixed sample (`sample.json`, sha256
`34b95ac6fe5ba63277e198dd05a0fa684c89e7e972fbd5c8e2012416e8421900`), run
against 3 LiteLLM-routed models confirmed live via `GET /v1/models`:

| model | pass rate | parsed | wall-clock |
|---|---|---|---|
| glm-5.3-flash | 1.00 (20/20) | 20/20 | 40.6s |
| deepseek-v4-flash | 1.00 (20/20) | 20/20 | 16.3s |
| qwen36-local | 1.00 (20/20) | 20/20 | 37.7s |

Full per-item results: `pilots/78590aee9360/results.json` (this branch).

**Pass-rate spread: 0 points** (all three models scored 100%). Below the
20-point win condition fixed in CARD.md by a wide margin.

## Reading the result

All three currently-routed models solved every item in this sample —
including the deliberately-invalid forms (affirming the consequent, denying
the antecedent, undistributed middle, false transitivity) meant to catch a
model that pattern-matches on surface form rather than reasoning. That is a
ceiling effect in the substitute sample, not evidence that the *proposed*
(unnamed, unlocated) arXiv benchmark itself would fail to distinguish
models — a harder or larger sample might still show a spread. What the pilot
does establish: at this difficulty, a small logical-inference check adds no
information beyond what Lumen already has, and there is no located,
reproducible benchmark artifact to build the proposal's script against in
the first place. Both facts support the original deferral reasoning
(narrowly scoped, general eval harness should come first) rather than
overturning it.

## RECOMMENDATION: SKIP

Pass-rate spread 0.00 pts (win condition: >= 20 pts). No hidden signal found
versus existing checks, and the proposal's own benchmark reference is not
locatable/pinnable, so there's nothing concrete to fold into a future
general eval harness beyond the pattern this pilot already produced
(fixed-sample + LiteLLM-client script, reusable if the harness needs it).
