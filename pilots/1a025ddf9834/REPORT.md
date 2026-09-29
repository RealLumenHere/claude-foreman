# PILOT REPORT — 1a025ddf9834: hybrid autoregressive-speculative inference for local models

wq#1951. Run 2026-09-29. Node: `lumen` (see deviation note below), against the fleet
LiteLLM gateway (`http://127.0.0.1:4000/v1`), draft model `lumen-lfm2-1b`, target model
`qwen36-local`.

## Deviation from CARD

CARD.md specifies node `hathor`. This worker's sandbox has no `ssh` in its allowlist and
no reachable path to hathor, so the pilot ran against the same fleet-wide LiteLLM
gateway from `lumen` instead — both nodes route the identical `lumen-lfm2-1b` /
`qwen36-local` model pool through the same proxy, so the measurement is of the models,
not of hathor-specific hardware. This is a real limitation, not a shortcut: if
hathor's local hardware serves these models differently (e.g. different quantization or
GPU), the numbers below may not transfer. Flagged for whoever reads ADOPT/WATCH forward.

## Method

`pilots/1a025ddf9834/run_pilot.py` runs both arms over the same fixed 5-prompt sample
(`pilots/1a025ddf9834/prompts.json`), `temperature=0`, `max_tokens=200`:

- **Baseline**: `qwen36-local` answers the prompt directly (`spec_decode.py:52-56`).
- **Treatment**: `lumen-lfm2-1b` drafts a full candidate answer, then `qwen36-local` is
  asked once to accept it verbatim or emit a correction (`spec_decode.py:59-77`). True
  per-token speculative decoding (accept/reject against the target's own logit
  distribution) is not implementable here: LiteLLM's `/chat/completions` surface does
  not expose per-token logits for these local models, so this sequence-level
  draft-then-verify scheme is the closest speculative-style approximation reachable
  through the existing gateway.

## Measured numbers

```
n:                        5
baseline_mean_s:          5.40
baseline_median_s:        2.90
treatment_mean_s:         6.06
treatment_median_s:       3.92
speedup_mean:              0.89   (>1.0 = treatment faster; <1.0 = treatment slower)
exact_match_rate:          0.20   (treatment text == baseline text, verbatim)
accepted_verbatim_rate:    0.80   (target accepted the draft's wording, but the draft's
                                    wording usually did not match what the target would
                                    have produced from scratch)
```

## Against the hypothesis

Hypothesis required treatment_mean_s <= 0.8 * baseline_mean_s (>=20% latency win) AND
exact_match_rate >= 0.8. Neither held: treatment was ~12% *slower* on mean latency
(0.89x), and exact match was 0.20, far below the 0.8 parity bar.

Root cause: draft-then-verify at the sequence level pays for two full model calls
(draft + verify) on the critical path whenever the draft isn't accepted verbatim, and
even the "cheap" accept path still costs a full target-model forward pass to *check* the
draft — there is no token-level short-circuit available through the chat-completions
API. The technique's real payoff (skipping most of the target's forward passes via
per-token accept/reject) requires logit-level access LiteLLM doesn't expose for these
local models, so this pilot could not build the thing the proposal actually described —
only the nearest black-box approximation, and that approximation loses.

## Kill criteria check

- Wall-clock: pilot completed in well under the 90 min budget.
- Baseline reproduced cleanly (no errors across 5 prompts, two independent runs).
- Treatment completed at budget — not a WATCH-by-timeout case; it's a clean negative
  result.

RECOMMENDATION: SKIP — measured treatment_mean_s=6.06s vs baseline_mean_s=5.40s (0.89x, slower not faster), exact_match_rate=0.20 (target 0.80); sequence-level draft-then-verify is the only variant reachable through LiteLLM's non-logit chat-completions API, and it loses on both latency and quality parity.
