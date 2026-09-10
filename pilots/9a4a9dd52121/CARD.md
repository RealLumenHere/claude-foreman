# PILOT 9a4a9dd52121 — Integrate DeLS‑Spec Parallel Speculative Drafting into LiteLLM Inference

Experiment card. Generated 2026-09-09T09:10:18+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `9a4a9dd52121` |
| Deferred | 2026-07-09 |
| Impact / effort / risk | M / M / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/9a4a9dd52121/REPORT.md` |

## Why this is a pilot and not a research prompt

reopened wq#1559 (form failure, never judged): Benefit is uncertain on CPU‑only hardware and we lack an evaluation harness to quantify latency gains; defer until a benchmark framework is in place.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

Draft-then-verify decoding (fast small model drafts, larger target model
verifies/corrects) reduces median end-to-end wall-clock latency per query by
at least 20% versus calling the target model alone, on this fleet's
LiteLLM-gateway-fronted CPU-only stack, with equivalent answer quality.

Filled in per the card's own "edit it before the run" clause, same as
wq#1560/ISSUE-LOG#257: `needs:` on this ticket was empty (confirmed:
`fleet-exec grant` -> `DENY [grant:grant_missing_env]`), so the card arrived
as the unfilled `EXPERIMENT_CARD.template.md`. This section and the two below
were written after a 2-call smoke test (`smoke.py`, kept in this dir) that
established both model names resolve on the gateway and gave a rough latency
scale (draft ~2.6s, target ~19.5s on a cold call) but before the scored
6-prompt run (`results.json`). The 20% win threshold is the generic
speculative-decoding claim size, not tuned to the observed gap — the observed
gap turned out to be a 46% *regression*, i.e. far outside the range a
tuned-to-win threshold would have been set to catch.

## Primary metric

Median wall-clock latency per query, in seconds, comparing:
- baseline: one call to the target model with the user prompt.
- treatment: one call to the draft model + one verify call to the target
  model, summed.

Win = treatment median <= 0.8x baseline median (>=20% faster) AND every
treatment answer matches the baseline answer in substance (both are correct/
equivalent, spot-checked by reading — this stack has no token-level verify
path, so exact-token equivalence, the real DeLS-Spec correctness bar, is not
testable here; see Treatment note).
WATCH = treatment median in [0.8x, 1.0x) baseline.
SKIP = treatment median >= 1.0x baseline (no latency benefit, possibly a
regression).

## Fixed sample

6 fixed one-sentence factual prompts, defined inline in `run_pilot.py:SAMPLE`
(capital of France, Python `len()`, water boiling point, HTTP acronym, gold's
chemical symbol, purpose of a git commit). Both arms run the identical 6
prompts, same order, in one process invocation. Sample and results are
checked into this pilot's directory (`results.json`) for reproduction.

## Baseline (measure FIRST)

<!-- The command that measures what Lumen does today. Run this before building the
treatment, so the baseline cannot be tuned to lose. -->

```
python3 /home/lumen/foreman/pilots/9a4a9dd52121/run_pilot.py
# baseline = one POST to $GATEWAY/v1/chat/completions, model=gemma4-e4b-local,
# per prompt in SAMPLE (run_pilot.py:call at line 47, invoked at run_pilot.py:77)
```

## Treatment

<!-- The smallest thing that tests the hypothesis. Not the full proposal — the
cheapest build that produces the primary metric. -->

Smallest build that tests the hypothesis given this stack's real
capabilities (see REPORT.md §Architecture note for why): a black-box,
completion-level approximation of draft-then-verify, NOT true token-level
DeLS-Spec (this gateway has no shared-KV-cache / logit-level verify hook
across two independently served chat models with different tokenizers).
Draft model = `hathor-lfm-1b` (LFM served from hathor, the CPU-only pilot
node named in the card — exercised via the gateway's own routing since
per-ticket hathor exec was unavailable, see REPORT.md). Target/verify model
= `gemma4-e4b-local` (closest configured gemma4 to the proposal's
"gemma4-12B" example; no 12B gemma4 is configured on this gateway —
confirmed via `list_models.py`, 37 models listed, no other gemma4 entry).

```
python3 /home/lumen/foreman/pilots/9a4a9dd52121/run_pilot.py
# treatment = draft call (hathor-lfm-1b) then verify call (gemma4-e4b-local,
# asked to accept-or-correct the draft), per prompt (run_pilot.py:80-88)
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

All inference is routed through the local LiteLLM gateway (docker, 127.0.0.1:4000) used by pipelines such as lumen-fleet-cerebras, minerva‑proposal, and minerva‑brief.

## Proposal — proposed change

Add a new service "lumen-speculative-decoder" that wraps the LiteLLM gateway, runs a fast draft model (e.g., LFM2.5‑1.2B) to generate speculative tokens and then validates them with the target model (e.g., gemma4‑12B) using DeLS‑Spec logic; update pipeline configs to call this service instead of the raw LiteLLM endpoint.

---

The report at `/home/lumen/foreman/pilots/9a4a9dd52121/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
