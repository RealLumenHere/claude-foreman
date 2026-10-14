# wq#3798: M80s LLM model selection — i7-12700, AVX2+AVX-VNNI, DDR4

## Hardware vs. the Mac baseline

`scratch/research/mac-llm-recommendations.md` sizes models for the Mac Pro 6,1 fleet:
Ivy Bridge-EP Xeons, **no AVX2**, ~50 GB/s DDR3 bandwidth. Every estimate in that doc is
capped by scalar/AVX1 matmul kernels, which is why it steers isis/hathor/wadjet toward
7B-14B dense models even though the Macs carry 64GB RAM — the ceiling there is compute,
not memory.

M80s (i7-12700, 12C/20T Alder Lake, AVX2 + AVX-VNNI, DDR4-3200 dual-channel) removes that
compute ceiling without raising the memory-bandwidth one:

| | Mac Pro 6,1 (baseline) | M80s |
|---|---|---|
| SIMD | AVX1/SSE only | AVX2 + AVX-VNNI (int8 dot-product in hardware) |
| Memory | DDR3, ~50 GB/s | DDR4-3200 dual-channel, ~50 GB/s |
| Decode (generation) ceiling | bandwidth-bound, ~50 GB/s / weight-GB | same formula, same ceiling — AVX-VNNI does not move more bytes/s |
| Prefill (prompt eval) | compute-bound on scalar/AVX1 kernels, the Mac doc's own stated bottleneck | AVX2/AVX-VNNI int8 GEMM is reported 30-500% faster than scalar/AVX1 llama.cpp prompt-eval on the same weights (justine.lol/matmul, llamafile tinyBLAS vs. stock llama.cpp) |
| RAM budget (target) | 64GB per Mac | 32-64GB (ticket spec) |

Net effect: **decode tok/s on M80s will land in roughly the same range the Mac doc
predicts for a given model size** (same bandwidth-over-weight-size arithmetic from that
doc's §2, `ideal tok/s ≈ 50 GB/s / weight_GB`) — AVX-VNNI does not change memory
bandwidth. What changes is **prefill**: M80s can afford a much longer prompt, or a
model with a heavier router/attention cost per prompt token, without paying the Mac
fleet's prompt-eval penalty. That is also exactly why a larger MoE (more total weight,
few active params per token) stops being the liability it was on isis (wq#3794: the
35B-A3B MoE was pulled from isis specifically because *prefill*, not decode, was the
measured bottleneck on no-AVX2 hardware) and becomes the natural fit for M80s's "heavy
compute + agent execution host" role.

## Why not a live Gemini/DeepSeek completion call

The ticket asks to send these specs to Gemini/DeepSeek. Neither path is reachable from
this fworker lane today (logged as ISSUE-LOG #1283, `kb/ISSUE-LOG.md`):
`gemini-3-flash` is `status: DARK` in `research/models.yaml:247` (gated on Frank's paid
AI Studio key landing, wq#2627), and `deepseek-v4-flash`'s scoped LiteLLM key lives in
the guard-denied secrets directory (wq#616) with no credential-helper wrapper to use it
without reading the raw file — unlike `lumen-github-identity.sh`'s pattern for GitHub.
This doc substitutes `fleet-search web`/`read` (SearXNG, no credential needed) for live
prior-art research instead, per the doctrine's mandatory prior-art step. Primary sources
used: justine.lol/matmul (AVX2/AVX-VNNI int8 GEMM speedup over scalar llama.cpp),
pinggy.io's Oct 6 2026 local-model memory-budget guide (model sizes/quants current as of
this ticket), and the Mac baseline doc above for the bandwidth-ceiling arithmetic.

## Recommendations

### Primary agent model (resident): `Qwen3-Coder-Next` (80B total / 3B active, Apache 2.0)

Q4 GGUF ≈ 46GB — fits a 64GB M80s with headroom for context cache and the OS, and per
the pinggy.io guide is the stated agentic-coding pick at the 64GB budget tier. The MoE's
3B active parameters keep decode near small-model speed (~50 GB/s / ~3B-active-equiv
weight-slice, not the full 46GB — expect low double-digit tok/s generation, consistent
with the Mac doc's §2 caveat that MoE decode must be estimated off active-expert weight,
not total size) while AVX2/AVX-VNNI prefill lets it actually use its context window on
real tickets instead of being prompt-eval-starved the way isis's 35B-A3B was on the Macs.
Needs direct benchmarking before being trusted as a number, not a planning estimate.

- Context: start 16384, raise to 32768 once `/health`/`/props` confirms served ctx holds
  a full fworker prompt (same discipline as `research/inference_nodes.yaml`'s
  `served_ctx` fields).
- Quant floor: Q4_K_M / UD-Q4_K_XL; the Mac doc's quantization-by-role table (§B) argues
  against going below Q4 for an agent role regardless of host — aggressive quantization
  costs more in retries than it saves in tok/s.

### Heavy reasoning model (on-demand, not resident): `Nemotron 3 Super 120B-A12B` (NVIDIA Nemotron Open Model License) — WITH A RAM CAVEAT

Q4 GGUF ≈ 64-72GB per the pinggy.io guide — at the **top** of M80s's 32-64GB RAM spec,
likely too tight to load at the upper end of that figure once OS + context cache are
counted, and outright will not fit at the 32GB end. Do not commit this as the default
without measuring actual RSS on the specific M80s RAM config (32 vs 64GB) first — this is
the same "do not guess a config value, verify it" instinct the Mac doc's own §2 caveats
model. Its draw is 1M context and more active parameters (12B) than the agent model's 3B,
which should mean materially better multi-step reasoning on research/RCA-shaped tickets
(the wadjet role on the Mac fleet) at the cost of slower decode (12B-active-equiv weight
slice vs. 3B).

**Fallback-if-it-doesn't-fit:** `Qwen3.8-27B` dense, 16-19GB at 4-bit, 262K context,
Apache 2.0 — the pinggy.io guide's "quality per GB" pick and small enough to stay
resident alongside the primary agent model simultaneously if M80s needs both loaded at
once, which Nemotron 3 Super cannot do on a 64GB box.

### Fallback model (resident, lightweight): `Qwen3.6-35B-A3B` (35B total / 3B active, Apache 2.0)

Q4 GGUF ≈ 23GB — small enough to coexist with the primary agent model's 46GB inside a
64GB budget if both need to be loaded together, and the pinggy.io guide's stated
"fastest all-rounder" at that size. Same 3B-active MoE shape as the primary pick, so its
decode-speed profile should track it closely; use it as the downgrade rung when the
primary agent model is busy/erroring, mirroring the Mac fleet's wadjet-fallback pattern
(`scratch/research/mac-llm-recommendations.md` §3 WADJET fallback: `qwen2.5-coder:7b`).

## What this doc does NOT do

No `research/inference_nodes.yaml` node was added for M80s. Per
`ops/wq3658-hardware-role-assignment-limpet-secondar/ROLE_ASSIGNMENT.md:91-96`, M80s's
physical/privileged provisioning (wq#3703/wq#3704/wq#3705) has not landed yet — adding a
`state: live` (or even `state: down`) registry node for a box with no OS/NVMe/unit
installed yet would be exactly the kind of unverified config value instinct 8 and the
doctrine's "never guess a path/config value" forbid. This doc is the model-selection
input for whichever provisioning ticket stands the node up, the same role
`scratch/research/mac-llm-recommendations.md` played for wq#3794's Mac reconfig.
