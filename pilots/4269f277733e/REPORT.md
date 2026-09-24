# PILOT 4269f277733e report — add GLM-5.2 to the LiteLLM gateway for local inference

Runner: orch-burn-2-4611f5 (wq#1984), 2026-09-24. No benchmark was run: the card's
precondition (the model is absent from the gateway) is false, so the hypothesis is moot.
Nothing was built or changed.

## What was measured

| check | result |
|---|---|
| `glm-5.2` anywhere in `/home/lumen/agent/litellm_config.yaml` | 0 mentions |
| GLM entries the gateway already carries | 3: `glm-5.3-flash` (OpenRouter), `glm-5.3-flash-zai` (Z.ai direct, the LPP brain since 2026-09-10), `glm-5.3-flash-local` (`ollama/hf.co/unsloth/GLM-5.3-Flash-GGUF:Q4_K_M`, `think: false`) |
| GLM on isis's ollama | not present; isis serves `qwen3.6:35b-a3b` and `qwen3:32b` |
| existing evaluation | wq#1573, `research/GLM53_FLASH_EVAL.md`: per-workload routing recommendation, e.g. keep the free `qwen36-local` for minerva defer-research because a paid GLM rung has no capability case there |

## Findings

1. The proposal is out of date. It says "no GLM-5.2 is available" and asks for a new model
   entry, but the gateway already carries the newer GLM-5.3-Flash in three forms, one of them
   a local GGUF. A GLM-5.2 rung would be a step back.
2. The stated config path (`/data/lumen/litellm/config.yaml`) does not exist here. The live
   file is `/home/lumen/agent/litellm_config.yaml`, mounted into the litellm container.
3. The deferral reason ("marginal value over existing local models, needs a benchmark showing
   it wins on Lumen's tasks") is what wq#1573 already answered for the 5.3-Flash generation.

## If someone still wants a local GLM comparison

Do it on `glm-5.3-flash-local` against `qwen36-local` using the wq#1573 sample, and only if a
concrete workload wants a local rung that qwen36-local cannot serve.

RECOMMENDATION: SKIP — superseded: GLM-5.3-Flash is already in the gateway (3 routes) and already evaluated (wq#1573); 0 GLM-5.2 references.
