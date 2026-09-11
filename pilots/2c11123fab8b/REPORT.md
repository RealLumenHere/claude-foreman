# PILOT REPORT 2c11123fab8b — Integrate Adaptive KV Cache Filtering into Local LLM Inference

Run: 2026-09-11, headless BUILD worker (wq#1682), worktree `wq1682-a1`.

## Node substitution (disclosed, precedent: ISSUE-LOG #257)

CARD.md says "Node: hathor." Hathor has been physically powered off/quarantined
since 2026-07-18 (`kb/ISSUE-LOG.md:41`, issue #33) pending Frank-at-the-bench
work (KVM console purchase, controlled bench boot) — not something a headless
BUILD worker can do or should block on under the full-autonomy standing order.
wq#1560, the first pilot run under this same lane, hit the identical problem and
resolved it by running both arms on Lumen itself against the same fixed sample
(`kb/ISSUE-LOG.md:292`, issue #257). This run follows that precedent: both the
baseline call and the (attempted) treatment target Lumen's actual local-inference
path, not a separate hathor sandbox. This does not change the outcome below —
the blocker is the inference engine's API surface, not the host running the
client.

## Baseline (measured)

Command: `pilots/2c11123fab8b/CARD.md` "Baseline" block, run directly against
the production Ollama backend that LiteLLM's `qwen36-local` model points at
(`foreman/ops/wq1623-qwen36-queue-guard/litellm_config_snippet.yaml:19-21`).
Raw response saved at `pilots/2c11123fab8b/results.json`.

| field | value |
|---|---|
| model | qwen3.6:35b-a3b (Q4_K_M, MoE, 3B active) |
| eval_count | 40 tokens |
| eval_duration | 80.149s |
| ms/token (eval) | 2003.7 |
| prompt_eval_count | 27 tokens |
| load_duration | 1.976s |
| total_duration | 84.682s |
| done_reason | `length` (truncated by `num_predict`, mid `thinking` block — this is a reasoning model; the 40-token cap cuts off before any final `response`, only `thinking` content was produced) |

This ms/token is unusually high for a 3B-active MoE model and most likely
reflects contention on the shared inference node rather than a property of KV
cache management — `qwen36-local`'s timeout was cut from 600s to 180s in
`foreman/ops/wq1623-qwen36-queue-guard/litellm_config_snippet.yaml:8-9` because
real callers were already routinely waiting 40-160s on this node. That
contention is a separate, already-tracked problem (wq#1623/wq#1588) and is not
what this pilot was asked to measure.

## Treatment — not buildable

The proposal's own "proposed change" section says it requires "either patching
vLLM/llama.cpp or adding a custom middleware that intercepts the KV cache
logic." Verified this run:

- Lumen's local models are served through Ollama (`api/tags` on
  `http://100.100.129.24:11434` returns `qwen3.6:35b-a3b`, `qwen3:32b`, both
  `format: gguf`), fronted by LiteLLM
  (`foreman/ops/wq1623-qwen36-queue-guard/litellm_config_snippet.yaml:19-21`,
  `model: ollama/qwen36`). Ollama wraps a compiled llama.cpp runtime; it is not
  vLLM, and its binary is not one this pilot lane can patch or rebuild.
- Ollama's `/api/generate` `options` object exposes only coarse, global knobs
  (`num_keep`, `num_predict`, `top_k`/`top_p`, `temperature`, `mirostat*`,
  `num_ctx`, `num_batch`, `num_gpu`, `num_thread`, …) — none of them select,
  prune, or adaptively filter individual KV cache entries per-token, which is
  what the proposal's technique does. The nearest real knob, a global KV-cache
  *quantization* type, is a different, non-adaptive technique and would not
  test this proposal's hypothesis even if it were exposed here (it is not, in
  this Ollama build's documented API).
- The node in question (`100.100.129.24`) serves live production traffic today
  (`qwen36-local`/`qwen36-live`,
  `foreman/ops/wq1623-qwen36-queue-guard/litellm_config_snippet.yaml`), so even
  if a patched engine existed, deploying it there to "test" would violate the
  pilot scope limit ("A pilot NEVER wires anything into prod," CARD.md:72-75).

This is exactly the integration gap the original defer named ("LiteLLM gateway
cannot directly modify per-model KV cache") and it is unchanged: no proven,
drop-in implementation of this technique exists for the engine Lumen actually
runs. Per the kill criteria, this is not a budget-exhaustion WATCH — it is an
architectural dead end for the current serving stack, so no treatment number
was fabricated.

## What would change this verdict

A drop-in patch or LoRA-style plugin for llama.cpp/Ollama itself (not vLLM,
which Lumen does not run for local models) that exposes adaptive KV filtering
as a request-time option, verified safe to load without modifying the binary
serving prod traffic. Re-pilot only if such an artifact appears.

RECOMMENDATION: SKIP — baseline ms/token measured at 2003.7 (eval_count=40,
eval_duration=80.149s, qwen3.6:35b-a3b via Ollama `100.100.129.24:11434`);
treatment not buildable on Lumen's actual local-inference stack (Ollama-wrapped
llama.cpp exposes no per-token KV cache filtering hook, and the only node that
runs it serves live prod traffic) — the defer's original blocker is confirmed
unchanged, not merely unmeasured.
