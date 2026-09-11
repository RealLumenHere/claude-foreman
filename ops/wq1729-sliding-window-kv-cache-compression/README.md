# wq#1729 — sliding-window KV cache compression: REJECT, not applicable to Lumen's stack

Proposal `a8a64d823f46` (source: [arXiv] Kara: Efficient Reasoning LLM Serving via
Sliding-Window KV Cache Compression) asked to configure sliding-window KV cache
compression for `gemma4-e4b-local` and `qwen36-local` "via LiteLLM config or
model-specific flags." The proposal's own decision was already `defer -
Uncertain applicability ... requires investigation before implementation`.
Factory plan `c8a0065dd995` skipped that investigation and staged a patch
adding `kv_cache_compression: true` to both models' `litellm_params` in
`/home/lumen/agent/litellm_config.yaml` (live host file, outside this repo —
same convention as wq#897/#948/#1299/#1406/#1590/#1591/#1623).

## Investigation (step 1 the plan skipped)

Both target models resolve to the Ollama backend, not vLLM:

- `qwen36-local` -> `ollama/qwen36` @ `http://100.100.129.24:11434`
  (`foreman/ops/wq1623-qwen36-queue-guard/litellm_config_snippet.yaml:19-21`).
- `gemma4-e4b-local` -> `ollama/hf.co/unsloth/gemma-4-E4B-it-GGUF:Q4_K_M` @
  `http://host.docker.internal:11434` (plan `c8a0065dd995` draft diff,
  `/home/lumen/proposals/a8a64d823f46/plan.md`).

A same-day sibling pilot already answered the exact question this proposal
deferred, for the architecturally identical case (a different KV-cache paper
technique, same serving path): `pilots/2c11123fab8b/REPORT.md:51-64` verified
that Ollama wraps a compiled llama.cpp runtime (not vLLM, not patchable from a
BUILD worker's sandbox) and that its `/api/generate` `options` object exposes
only coarse global knobs (`num_keep`, `num_predict`, `top_k`/`top_p`,
`temperature`, `mirostat*`, `num_ctx`, `num_batch`, `num_gpu`, `num_thread`,
...) — no per-request or per-model hook that selects, prunes, windows, or
otherwise compresses KV cache entries. The nearest real Ollama knob is a
global KV-cache *quantization* type (server-startup env var, not a
per-request or per-model LiteLLM param), which is a different, non-sliding-window
technique and isn't exposed through `litellm_params` either way.

`kv_cache_compression` is not a real `litellm_params` key: it is not one of
LiteLLM's own config fields, and it is not an Ollama or OpenAI-compatible API
parameter, so LiteLLM would either drop it silently or forward it as an
unrecognized kwarg to a backend that doesn't understand it. Applying the
staged patch would not compress anything — it would add a no-op field and
create a false paper trail that this technique is live.

## Decision

REJECT `a8a64d823f46` / do not apply plan `c8a0065dd995` as drafted. This
mirrors pilot 2c11123fab8b's SKIP for the sibling proposal: the blocker is
architectural (Ollama-wrapped llama.cpp exposes no such knob today), not a
tuning or config problem, so no flag exists that would make this real.

**What would change this verdict:** a drop-in patch or plugin for llama.cpp/
Ollama itself (not vLLM, which Lumen does not run for local models) that
exposes sliding-window KV cache compression as a request-time or server
option, verified safe to load without modifying the binary serving prod
traffic (`100.100.129.24` serves live `qwen36-local`/`qwen36-live` traffic
today per `foreman/ops/wq1623-qwen36-queue-guard/litellm_config_snippet.yaml`).
Re-propose only if such an artifact appears upstream in Ollama/llama.cpp.

## Sandbox note (ISSUE-LOG #240/#241 same class)

`minerva-factory` (`/home/lumen/minerva/minerva_factory.py`) is not in this
BUILD worker's Bash allow-list and its backing file resolves outside every
allowed directory for this session, so `minerva-factory close a8a64d823f46
--reject "..."` cannot be run from here — same gap already logged for
wq#1503/wq#1507. The git-side deliverable (this investigation, logged below)
is complete; the `minerva-factory close --reject` call is an
orchestrator-only follow-up.
