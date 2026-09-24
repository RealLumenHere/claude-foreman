# PILOT 67af7c7e9ecf report: semantic uncertainty-guided routing

Runner: orch-burn-C (wq#2446), 2026-09-24. Nothing wired into prod. Spec, verdict rule and sample were written into CARD.md before the first scored call (blank original kept at data/CARD.blank.orig).
Node deviation: hathor was unusable (load ~12, fworker wq#2495 on hathor-llama8b, all hathor groups timed out at 100 s), so the run is API calls from Lumen through LiteLLM. Latency below is gateway wall time, one request at a time.

## Sample and method
200 TriviaQA rc.nocontext validation rows, sha256 005ee24de2177a10ad988744b532184c87a6ff4972ced4e9836fa8184a2ac3bf. Prompt 'Answer with only the answer, no explanation.', temperature 0, max_tokens 24. Grader: normalized alias containment, fixed. Uncertainty U = 1 - exp(mean token logprob of the answer). Weak model qwen36-local (isis llama.cpp), strong model deepseek-v4-flash. Baseline arm (no logprobs) ran first, then the treatment arm (logprobs=true, top_logprobs=5).

## Results (data/analyze.out, data/results.json)
| | qwen36-local | deepseek-v4-flash |
|---|---|---|
| accuracy, baseline / treatment | 0.560 / 0.560 | 0.790 / 0.790 |
| same answer both arms | 100% | 95% |
| median latency, baseline / treatment | 1.380 s / 1.408 s | 0.734 s / 0.731 s |
| latency ratio | 1.021 | 0.996 |
| AUROC of U for wrong answers | 0.834 (95% CI 0.777-0.888) | 0.500 (constant U, see below) |

Routing simulation, escalate the 30% most uncertain qwen36-local answers to deepseek-v4-flash (offline from the collected answers):
weak only 0.560, strong only 0.790, uncertainty-routed 0.675, random-30% expected 0.629, oracle 0.735. Gain over random +4.6 points (bootstrap 95% CI +0.9 to +7.4). Pre-registered bar was +5.

Post-hoc, descriptive only, NOT used for the verdict (data/analyze_extra.out): gain over random by budget 10% +3.2, 20% +4.4, 30% +4.6, 40% +5.3, 50% +6.5, 60% +6.7 points. At 30% the uncertainty ordering captures about 66% of the oracle gain (random captures 39%).

## Capability finding: where logprobs are real
8-token probe of every LiteLLM group (research/wq2446_litellm_logprobs_probe.{py,out}), then a value check.
- REAL logprobs: qwen36-local, qwen36-live (isis llama.cpp; e.g. chosen token -0.01, alternatives -4.77, -7.33). nephthys-qwen14b returns a logprobs block but its values were not inspected.
- DEGENERATE: deepseek-chat, deepseek-v4-flash and messenger-chat (the Messenger brain) return logprob 0.0 for every chosen token and -9999.0 for every alternative. The field is present, the numbers are placeholders. This is why deepseek AUROC is exactly 0.500 (U is 0 for all 200 answers). The first probe counted these as 'OK'; that was wrong and is corrected here.
- NONE (HTTP 200, no logprobs field): the three Cerebras groups, glm-5.3-flash, glm-5.3-flash-zai, kimi-k3, openrouter-free, deepseek-v4-pro, messenger-re, isis-qwen36.
Consequence: the signal is only computable on local llama.cpp models, which fits the weak-local, strong-cloud escalation shape simulated here. It cannot be computed on the Messenger brain, the Cerebras rungs or any verify/critic gate route through the gateway today.

## Against the pre-registered rule
AUROC >= 0.70: PASS (0.834). Latency ratio <= 1.10: PASS (1.021). Routed gain >= 5 points at 30%: MISS (+4.6; CI straddles the bar). ADOPT needs all three. SKIP needs AUROC < 0.60. Result: WATCH.

## Caveats
n = 200 on one factual-QA task, one weak/strong pair, one prompt. Latency compares two sequential runs, not interleaved, and isis also serves fleet traffic. same_answer 95% on deepseek shows the API is not fully deterministic at temperature 0. TriviaQA correctness by alias containment is lenient. A real adoption trial should use Lumen's own routed task types with a labelled outcome.

## If someone picks this up
Smallest first change: a library function that turns llama.cpp logprobs into U for calls to qwen36-local/live, logged next to the existing verify outcome so the escalation threshold can be tuned on real Lumen tasks. Do not build it on DeepSeek or Cerebras logprobs.

RECOMMENDATION: WATCH - qwen36-local AUROC 0.834 (95% CI 0.777-0.888), logprobs latency ratio 1.021, routed gain +4.6 pts at 30% escalation (bootstrap CI +0.9..+7.4, bar was +5); DeepSeek/Messenger logprobs are placeholder zeros, real logprobs only on local llama.cpp.
