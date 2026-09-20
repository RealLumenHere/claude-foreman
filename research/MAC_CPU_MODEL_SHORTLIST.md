# wq#2245 — best LLM + engine + quant for the AVX-only Mac Pro 6,1 boxes

Desk research that decides what goes into the bench matrix (sibling ticket) and in
what order. Every claim is dated; this field moves monthly. Research date
**2026-09-20**, against llama.cpp `master` and ik_llama.cpp `main` as of that day.

Target: Mac Pro 6,1 (hathor, nephthys, isis, khepri). Xeon E5-2697 v2, 12C/24T,
**AVX + F16C, no AVX2, no FMA, no AVX-512**; 4x16 GB DDR3-1866 quad channel
(59.7 GB/s peak, ~39 GB/s stream); 64 GB; RAPL cap 75 W; single socket so NUMA is
not a factor; GPUs unusable (2 GB FirePro).

Job: agentic coding worker driven by the Claude Code CLI through the LiteLLM
gateway. First request ~4-5.5k tokens (system prompt + 6 tool schemas + ticket),
then many tool-call turns. Needs reliable tool calling and >=32k ctx.
**Prefill rate, not decode, gates this workload.**

---

## 0. The five findings that decide the matrix

1. **ik_llama.cpp is not an option on AVX1 today.** The AVX1 patch is PR #2138,
   still **open** since 2026-07-15; the maintainer reproduced NaN logits from it
   and wrote "I don't think the PR actually works" (§2.1). Its own author's
   measured prefill — 16 tok/s on *dual* E5-2690 v2 — is a quarter of what stock
   llama.cpp already does on *one* of our sockets.
2. **Stock llama.cpp has a first-class AVX1 target and we are already on it.**
   `ggml_add_cpu_backend_variant(ivybridge SSE42 AVX F16C)` is exactly this CPU
   (§2.2). Nothing needs patching.
3. **The quant advice for this box is the opposite of the usual advice.** The
   tiled GEMM that carries prefill (`llamafile_sgemm`) has a real AVX1 path, but
   only for **Q8_0, Q4_0, Q5_0, IQ4_NL** (plus F16/BF16/F32). Every K-quant and
   every grid IQ-quant misses it on *all* ISAs, and the 8x8 repack GEMM and the
   new IQ-panel GEMM are both AVX2-gated. The fleet runs Q4_K_M today, which is
   the one class that gets no tiled kernel at all (§3).
4. **Qwen3.6-35B-A3B cannot reuse a prompt prefix.** It is a recurrent hybrid
   (30 DeltaNet + 10 attention layers). llama.cpp disables `--cache-reuse` and
   context shift outright when the memory can't shift, and there is an open bug
   (#26994, 2026-08-13) reporting that the longest-common-prefix is found and
   then discarded on `qwen35`/`qwen35moe` (§4.3). Our workload sends a
   byte-identical system prompt every ticket and then appends a tool turn at a
   time — the single biggest available prefill lever is switched off for the
   model we currently run.
5. **Gemma 4 26B-A4B is not a coding worker.** SWE-bench Verified **17.4** against
   Qwen3.6-35B-A3B's 73.4 (§6). It is still worth one bench row, but as an
   *engine probe* — it is the only model in the class with official QAT **Q4_0**
   GGUF weights, i.e. the one quant format with an AVX1 tiled kernel.

Test order: **R1 → R2 → R3 → R5** settles the argument. Everything below R5 is
contingent.

---

## 1. The measurements we are extrapolating from

One hard anchor on this exact hardware, plus two dense controls:

| box | model | engine | prefill tok/s | decode tok/s | source |
|---|---|---|---|---|---|
| isis | qwen3.6-35b-a3b Q4_K_M | llama-server | **63** | **10.9** | `lumen_state isis.qwen36_bench`; `research/QWEN36_LOCAL_WORKER_SERVING.md:16` |
| hathor | llama3.1:8b dense | ollama | 13.6-15 | 11-16 | `research/QWEN36_LOCAL_WORKER_SERVING.md:14` |
| nephthys | qwen3:14b dense | ollama | 7.4-8 | 3.8-5 | `research/QWEN36_LOCAL_WORKER_SERVING.md:15` |

Earlier, co-tenanted, worse: a 4,910-token prompt spent 277 s in prompt eval at
17-20 tok/s (`research/LOCAL_MODEL_STRATEGY.md:149-151`). That is the failure
mode the matrix exists to avoid.

Decode on CPU is bandwidth-bound: `tok/s ~= stream GB/s / bytes-per-token`, with
`bytes-per-token ~= active_params x 0.58` at Q4_K_M
(`research/LOCAL_MODEL_STRATEGY.md:99-108`). Back-solving the isis anchor —
10.9 tok/s at ~2.0 GB/token — gives an **effective ~22 GB/s** for MoE expert
gather on DDR3-1866, well under the ~39 GB/s clean-stream figure. Expert gather
is a scatter of small reads, not a stream; §7 uses 22 GB/s, not 39, for every
MoE decode estimate. Prefill is compute-bound and has no such closed form —
prefill numbers below are scaled off the 63 tok/s anchor by active parameters
and flagged as extrapolation.

**KV correction.** `research/QWEN36_LOCAL_WORKER_SERVING.md:63-66` estimates
~96 KiB/token for Qwen3.6-35B-A3B and asks for verification. From the model card
(10 gated-attention layers, 2 KV heads, head_dim 256) the true figure is
**20 KiB/token** — 10 x 2 x 256 x 2 x 2 bytes = 20,480 — so a 32k slot costs
**~0.64 GiB** f16, not ~3 GiB, plus ~63 MB of constant DeltaNet state per slot.
96 KiB/token is the right number for a *classic* 48-layer/4-KV-head model, i.e.
Qwen3-Coder-30B-A3B. The slot budget in that doc is ~5x too conservative for the
model it plans to serve.

---

## 2. Engine on AVX1

### 2.1 ik_llama.cpp — no

| fact | evidence | date |
|---|---|---|
| AVX1 support is an unmerged PR, not a feature | ik_llama.cpp PR **#2138** "ggml : fix build and MoE inference on CPUs without AVX2 (AVX1-only)", state **open** | opened 2026-07-15, still open 2026-09-20 |
| Maintainer reproduced NaN logits from the PR | ikawrakow, in-thread: built with `-DGGML_IQK_MUL_MAT=OFF -DGGML_IQK_FLASH_ATTENTION=OFF -DGGML_NATIVE=OFF -DGGML_AVX2=OFF -DGGML_ARCH_FLAGS="-march=ivybridge"`, "It builds fine but then I get NaN logits" and later "I don't think the PR actually works" | 2026-07-17, 2026-07-26 |
| Independently reproduced on real AVX1 iron | mb8565, Xeon E5-2440 v2 (avx, no avx2, no fma), same flags + `-DGGML_FMA=OFF`: `-nan`. Traced to `.vec_dot_type` entries in `ggml.c`'s `type_traits` that predate the branch; the branch makes that path reachable | 2026-07-27 |
| Third user, different failure | stash763, dual Opteron 4386, qwen3-coder-next: `std::runtime_error: Failed to parse input at pos 0` (core dumped) | 2026-07-21 |
| Maintainer will not carry AVX1 | "I don't have access to such a CPU... inevitably the AVX build will break again as we go" | 2026-07-15 |
| `--run-time-repack` corrupts output on AVX1 | neomindlabs: reorders weights into `Q8_0_R8`, an AVX2-only interleaved layout, "which garbles output on AVX1 the same way. That's a separate bug, and the patch doesn't try to fix it" | 2026-06-08 |

The build fix does work — at `504bd70a` the tree compiles on AVX1 — and one hunk
(`#include <cstdint>` in `iqk_common.h`) is needed even with IQK left **on**,
because nothing else pulls `<cstdint>` in when the wide-intrinsic headers are
absent. That matters to someone building ik_llama.cpp for a *GPU-offloaded* box
with an old CPU. It does not help us: we have no usable GPU, so the CPU must do
the matmuls, and that is the path that returns NaN.

The performance case is gone too. The PR author's own end-to-end result is
**~5.2 tok/s decode and ~16 tok/s prompt-eval** on Gemma 4 26B-A4B **Q8_0**,
on **dual** E5-2690 v2 (20 cores, 2 memory controllers). We measure **63 tok/s
prefill** on a *single* E5-2697 v2 with stock llama-server. Different model and
quant, so this is not a clean A/B — but a 4x prefill gap on 2x the cores is not
a gap a merge would close.

**Verdict: no ik_llama.cpp rows in the matrix.** Revisit only if #2138 merges
*and* a non-maintainer posts numbers on AVX1 silicon.

### 2.2 Stock llama.cpp — yes, and it already targets this CPU

`ggml/src/CMakeLists.txt:494-506`:

```
ggml_add_cpu_backend_variant(x64)
ggml_add_cpu_backend_variant(sse42        SSE42)
ggml_add_cpu_backend_variant(sandybridge  SSE42 AVX)
ggml_add_cpu_backend_variant(ivybridge    SSE42 AVX F16C)
ggml_add_cpu_backend_variant(piledriver   SSE42 AVX F16C FMA)
ggml_add_cpu_backend_variant(haswell      SSE42 AVX F16C FMA AVX2 BMI2)
...
```

`ivybridge` = SSE42 + AVX + F16C, which is exactly the E5-2697 v2 feature set.
It was requested in issue **#17966** (2025-12-12) and landed by 2025-12-28; the
shipped artifact is visible in the wild by 2026-06 (`ggml-cpu-ivybridge.dll`
appears in #24558). We do not need a patched fork or a custom build target —
`-DGGML_NATIVE=ON` selects it.

### 2.3 Ollama's bundled runner — no

Already measured against llama-server on identical hardware: 13.6-15 tok/s
prefill (llama3.1:8b) and 7.4-8 (qwen3:14b) versus 63 for the MoE under
llama-server. Ollama 0.32.6 also forces a single slot for this architecture,
which is why isis runs `llama-qwen36.service` on :8095 directly
(`research/QWEN36_LOCAL_WORKER_SERVING.md:21-26`). Keep it installed for
rollback, per §2.5 of that doc; do not bench it.

### 2.4 What stock llama.cpp *loses* on AVX1

Three fast paths compile out. This is the honest cost, read from source:

| path | file | gate | on AVX1 |
|---|---|---|---|
| 8x8 repack GEMM (`ggml_gemm_q4_0_8x8_q8_0`, `_q4_K_`, `_iq4_nl_`, `_mxfp4_`, `_q2_K_`) | `ggml/src/ggml-cpu/arch/x86/repack.cpp` | `__AVX2__` / `__AVX512F__` only | **unavailable** |
| IQ panel GEMM (`ggml_compute_forward_mul_mat_iqp`, `_id_iqp`) | `ggml/src/ggml-cpu/iqp.cpp` | `__AVX2__`, best with `AVX512_VNNI`/`AVXVNNI` | **unavailable** |
| tinyBLAS tiled GEMM | `ggml/src/ggml-cpu/llamafile/sgemm.cpp` | `__AVX2__ \|\| __AVX512F__ \|\| __AVX__` | **available** — see §3 |

What survives is the per-row `ggml_vec_dot_*` family, and those are in good
shape: every quant type in `arch/x86/quants.c` except the ternary TQ1_0/TQ2_0
carries a hand-written `#elif defined(__AVX__)` branch — Q4_0, Q4_1, Q5_0, Q5_1,
Q8_0, **MXFP4**, NVFP4, Q2_K through Q6_K, IQ1_S/M, IQ2_XXS/XS/S, IQ3_XXS/S,
IQ4_NL, IQ4_XS. So on AVX1 nothing falls to scalar; it falls to *untiled*.

---

## 3. Quant on AVX1 — which formats get a tiled kernel

`llamafile_sgemm` is the only tiled GEMM we can reach. Its type dispatch
(`sgemm.cpp:4041-4135`) admits, under `#if defined(__AVX2__) || defined(__AVX512F__) || defined(__AVX__)`:

| GGML type | tinyBLAS on AVX1 | vec_dot AVX1 branch |
|---|---|---|
| F32 / F16 / BF16 | yes | — |
| **Q8_0** | **yes** | yes |
| **Q4_0** | **yes** | yes |
| **Q5_0** | **yes** | yes |
| **IQ4_NL** | **yes** | yes |
| Q4_K / Q5_K / Q6_K / Q2_K / Q3_K | **no** (not in dispatch on any ISA) | yes |
| IQ4_XS, IQ2_*, IQ3_*, IQ1_* | **no** | yes |
| MXFP4 (gpt-oss), NVFP4 | **no** | yes |
| TQ1_0 / TQ2_0 | no | **no** — AVX2 only, do not use |

Two caveats that stop this from being a free win, both read from source and both
things the bench must settle rather than assume:

- **`llamafile_sgemm` is called only from `ggml_compute_forward_mul_mat`**
  (`ggml-cpu.c:1306,1389`), **never from `ggml_compute_forward_mul_mat_id`**
  (`ggml-cpu.c:1550`). MoE expert FFNs go through `mul_mat_id`. So on an
  A3B-class model the tiled path can only touch the non-expert tensors —
  attention projections, the dense first layer, shared-expert tensors that the
  graph emits as plain `mul_mat`, and `lm_head`. On Qwen3.6 that is a minority of
  the FLOPs. The win is real but bounded, and its size is exactly what R2 measures.
- Quantising *to* Q4_0 from an existing Q4_K_M costs quality twice. Requantise
  from BF16/Q8_0 source, or use a published IQ4_NL build.

That makes **IQ4_NL the interesting 4-bit format for this box**: it is in the
AVX1 tinyBLAS dispatch, and `unsloth/Qwen3.6-35B-A3B-GGUF` already publishes
`Qwen3.6-35B-A3B-UD-IQ4_NL.gguf` at **18.0 GB**, against 22.1 GB for the
`UD-Q4_K_M` the fleet runs. Smaller *and* on the tiled path.

Q8_0 gets the tiled path too, but at 36.9 GB for this model it doubles
bytes-per-token, and decode is bandwidth-bound — it would trade ~11 tok/s decode
for ~6. Only worth a row if IQ4_NL shows a large prefill win and we want to know
whether the tiled kernel or the bit-width is responsible.

---

## 4. Prefill levers on this CPU

### 4.1 Batch and ubatch

`-b/--batch-size` (logical) and `-ub/--ubatch-size` (physical) — `common/arg.cpp:1666,1673`.
`-ub` is the one that matters on CPU: it sets how many tokens are in flight per
matmul, which is what gives the kernels row reuse and amortises the q8
quantisation of activations. It also sets the compute-buffer size. Sweep
`-ub 256/512/1024/2048` with `-b` at or above `-ub`. This is R5 and it is cheap —
one model load, four `llama-bench` runs.

### 4.2 Threads

12 physical cores, 24 with HT. llama.cpp defaults to the physical count. Prefill
is compute-bound, which is the one case where SMT sometimes helps; decode is
bandwidth-bound, where it usually hurts. Bench `-t 12` against `-t 24` **for
prefill specifically** — do not carry a decode-derived answer over. Do not bench
this from inside a worker session: the fworker cgroup is CPU-capped at ~4 cores
and underreports by roughly 5x (ISSUE-LOG row 315,
`research/LOCAL_MODEL_STRATEGY.md:83-87`).

### 4.3 Prefix / slot cache reuse — the big one, and it is model-dependent

Three mechanisms, in increasing order of how much they save:

- **Slot selection by LCP.** `server-context.cpp:1584` picks the slot with the
  longest common prefix against the incoming prompt. Always on.
- **`--cache-reuse N`** — reuse cached chunks via KV shifting.
- **`--slot-save-path`** — persist slot state to disk.

`--cache-reuse` and context shift are both gated on `llama_memory_can_shift()`
(`server-context.cpp:1185-1195`). When it returns false the server prints
`cache_reuse is not supported by this context, it will be disabled` and silently
drops the flag. Recurrent and hybrid memories cannot shift.

Which of our candidates are hybrid, from `src/llama-arch.cpp:1075+`
(`llm_arch_is_hybrid`): **QWEN35MOE (Qwen3.6-35B-A3B)** and **NEMOTRON_H_MOE**
are. Qwen3-Coder-30B-A3B (`qwen3moe`), GLM-4.7-Flash (`glm4moe`), Gemma 4
(`gemma4`) and gpt-oss are not.

Independent confirmation from the field: llama.cpp **#26994** (open, 2026-08-13,
no maintainer reply as of 2026-09-20) — "every request re-encodes the [prefix]"
on Qwen3.6 27B dense and 35B MoE, "works as expected on Qwen3-VL (non hybrid)",
"Qwen3.6 is hybrid attention + SSM, so the memory is recurrent and cannot roll
back to an arbitrary position. The LCP is found, then discarded."

There *is* a bounded rollback — `common_context_can_seq_rm` returns
`COMMON_CONTEXT_SEQ_RM_TYPE_RS`, "bounded partial sequence removal", limited to
`llama_n_rs_seq(ctx)` tokens (`common/common.cpp:1583-1592`). That bound is sized
for speculative-decoding rollback, not for a multi-thousand-token conversation
delta. For our turn structure it will not help.

**Cheap verification, 30 seconds, do it first:** start `llama-server` with
`--cache-reuse 256` on each candidate and grep the startup log for
`cache_reuse is not supported`. That one line partitions the model list.

### 4.4 Flash attention on CPU

CPU FA exists and has two kernels (`ggml/src/ggml-cpu/ops.cpp`): a per-row
`_one_chunk` path and a **tiled** path. The tiled one is selected only when
(`ops.cpp:9316-9329`):

```
q->type == GGML_TYPE_F32 && kv_is_f32_or_f16 && k->type == v->type && neq1 >= Q_TILE_SZ
```

Two consequences:

- The tile condition `neq1 >= Q_TILE_SZ` means the tiled kernel engages during
  **prefill** and not during decode. That is precisely the half we care about.
  Run with `-fa on` (default is `auto`).
- **`kv_is_f32_or_f16` means quantising the KV cache turns the tiled kernel
  off.** `-ctk q8_0 -ctv q8_0` still works — CPU FA reaches quantised K/V
  through the generic `vec_dot`/`to_float` traits (`ops.cpp:8688-8694`) — but it
  drops you back to the per-row kernel. On a 64 GB box serving models whose KV
  is 20-96 KiB/token, **do not quantise KV by default**. It buys RAM we have and
  costs prefill we don't.

`DV % f32_epr == 0` is also required; `f32_epr` is 8 on AVX. All candidate head
dims (256, 128, 64) satisfy it.

### 4.5 Speculative decoding / MTP

Qwen3.6-35B-A3B ships an MTP head ("MTP: trained with multi-steps", model card)
and GLM-4.7-Flash has `num_nextn_predict_layers: 1`. Both are **decode** levers.
They do nothing for prefill, which is our binding constraint. Park them: worth a
single follow-up row *after* a model wins on prefill, not a reason to pick one.

### 4.6 Reasoning preservation — a prefill lever hiding in the reasoning flags

`--reasoning-preserve` keeps the reasoning trace for the *whole* history rather
than just the last assistant message, and it is **enabled by default**
(`common/arg.cpp:3722-3726`). In an agentic loop with a dozen tool turns that
inflates every subsequent prompt with every previous think block — paid at
prefill rate, on the machine where prefill is the bottleneck.
**`--no-reasoning-preserve` on every row**, except where a model's card demands
otherwise (GLM-4.7-Flash asks for Preserved Thinking for multi-turn agentic
work — so for GLM this is a measured trade, not a default).

---

## 5. Thinking control through an OpenAI-compatible endpoint

The fleet previously had to send `reasoning effort: none` to stop qwen3 thinking
at 1.6 tok/s. That is no longer the supported mechanism, and it is not reliable.

**Use the server-side flags** (`common/arg.cpp:3666-3726`):

| flag | effect |
|---|---|
| `-rea, --reasoning on\|off\|auto` | the supported switch; default `auto` = detect from template |
| `--reasoning-budget N` | `0` = end thinking immediately; `-1` = unrestricted |
| `--reasoning-format none\|deepseek\|deepseek-legacy` | where thoughts land in the response |
| `--no-reasoning-preserve` | drop traces from full history (§4.6) |

`--chat-template-kwargs '{"enable_thinking":false}'` still works but now logs
`Setting 'enable_thinking' via --chat-template-kwargs is deprecated. Use
--reasoning on / --reasoning off instead.`

Per-request control is weaker than it looks. `chat_template_kwargs` is forwarded
(`server-chat.cpp:579`) and `reasoning_effort` is plumbed
(`server-chat.cpp:290`), but **`reasoning_effort` does not yet control
`enable_thinking`** — that is PR **#27221**, open since 2026-08-17. Per-request
`enable_thinking` via `extra_body` is issue **#22336**, open since 2026-04-24.
Whether LiteLLM forwards `chat_template_kwargs`/`extra_body` to the backend at
all is a second, separate question.

**So: set `--reasoning off` per deployment on the llama-server unit; do not rely
on a per-request field.** If a model needs thinking on for some archetypes, that
is a second deployment, not a request flag.

Model-specific, dated:

- **Qwen3.6**: `tool_choice` named/required is **not enforced** when
  `enable_thinking=false` — llama.cpp **#27767**, open 2026-08-26. If the
  gateway pins a tool, verify it actually fires.
- **GLM-4.7-Flash**: card asks for Preserved Thinking mode for multi-turn
  agentic tasks, so `--reasoning off` may cost it its headline τ²-Bench score.
- **gpt-oss-20b**: native low/medium/high reasoning effort in its harmony format —
  the one model here where effort is a first-class model feature rather than a
  template kwarg.

---

## 6. Models — the small-active MoE class, ranked for this job

Architecture read from each model's `config.json`; scores from the model cards
as cited. Vendor self-reports with their own agent scaffolds; treat as ordering,
not as absolute.

| model | total/active | layers | KV bytes/token (f16) | 32k KV | licence | agentic score | llama.cpp arch |
|---|---|---|---|---|---|---|---|
| **Qwen3.6-35B-A3B** | 35B / ~3.3B | 40 = 30 DeltaNet + 10 attn (16Q/2KV, hd 256) | **20 KiB** | 0.64 GiB | Apache-2.0 | SWE-V **73.4**, SWE-Multiling 67.2, SWE-Pro 49.5, TB2.0 **51.5** | `qwen35moe` — **hybrid** |
| **Qwen3-Coder-30B-A3B-Instruct** | 30B / ~3.0B | 48 attn (32Q/4KV, hd 128), 128 exp / 8 | **96 KiB** | 3.0 GiB | Apache-2.0 | none on card ("excels in tool calling") | `qwen3moe` — plain |
| **GLM-4.7-Flash** | ~30B / ~3.6B | 47, MLA (kv_lora 512 + rope 64), 64 exp / 4 + 1 shared | ~**53 KiB** | 1.65 GiB | **MIT** | SWE-V **59.2**, τ²-Bench **79.5** | `glm4moe` — plain |
| **Nemotron-3-Nano-30B-A3B** | 30B / ~3.5B | 52, pattern `MEMEM*E…` = 6 attn (2KV, hd 128) + Mamba2 | **6 KiB** | 0.19 GiB | NVIDIA Open (`other`) | SWE-bench (OpenHands) 38.8, BFCL v4 53.8, τ²-v2 avg 49.0, TB-hard 8.5 | `nemotron_h_moe` — **hybrid** |
| **gpt-oss-20b** | 21B / 3.6B | 24 = 12 sliding(128) + 12 full (64Q/8KV, hd 64) | **24 KiB** | 0.77 GiB | Apache-2.0 | **no agentic coding score on card** | `gpt-oss` — plain |
| **Gemma 4 26B-A4B-it** | 25.2B / ~3.8B | 30 = 25 sliding(1024) + 5 full (16Q/8KV, hd 256) | **40 KiB** | ~1.5 GiB | Apache-2.0 | SWE-V **17.4** (Qwen's table) | `gemma4` — plain |

Dense controls, all already disqualified by measurement on this box:

| model | why not |
|---|---|
| Qwen3.6-27B dense | 27B streamed per token ~15.7 GB at Q4_K_M → ~1.4 tok/s decode at 22 GB/s. SWE-V 75.0 is the best number in the table and it is unreachable here. |
| Devstral-Small-2-24B (Apache-2.0) | same arithmetic, ~1.6 tok/s. |
| Qwen3 14B | **measured**: 7.4 prefill → a 4.5k-token first request is ~10 minutes of TTFT. |
| Qwen3 8B | **measured**: 13.6 prefill → ~5.5 minutes TTFT. |

### Tool-calling reliability, by model, with open bugs named

This is where a Claude-Code-style harness actually breaks, and it is
parser/template territory, not model capability.

- **Qwen3.6** — #27767 (open 2026-08-26) `tool_choice` named/required not
  enforced when `enable_thinking=false`. #26817 (open 2026-08-09) temp-0 tool
  calling nondeterministic for hybrid Qwen3.6-A3B, flips with prompt-cache state
  (reported on Vulkan; the prompt-cache coupling is backend-independent enough to
  be worth watching on CPU).
- **Qwen3-Coder** — #26987 (open 2026-08-12) lazy tool-call trigger never fires
  when the model emits neither `<tool_call>` nor `<function=`. Long history:
  #27363, #26530 (template fails to trigger XML tool calls with *large* prompts —
  directly our case), #19382, #19513. The dedicated parser landed in #16755.
  Most are closed; the open one is a silent-drop failure mode, which is the worst
  kind for an agent loop.
- **gpt-oss** — #19553 (open 2026-02-12) double-escaping in tool call arguments
  and responses. Multi-turn reasoning + tool role Jinja errors were fixed
  (#19704/#19702/#21213), so the remaining risk is argument escaping.
- **Gemma 4** — the longest bug tail of the set (#29089, #25100, #25072, #22786,
  #22371, #21680, #21642), mostly closed, plus #23247 (open) for
  schema-constrained decoding. The PEG-based parser has been repeatedly fragile.
- **GLM-4.7** — #19009 only ("template supports tool calls but does not natively
  describe tools"), closed. Cleanest record here, on the smallest sample.
- **Nemotron** — #26737 (closed) `tool_choice: required` grammar stack. Thin
  sample.

---

## 7. The ranked test matrix

Expected columns are **extrapolations** anchored on isis's measured 63/10.9 for a
3.3B-active MoE at Q4_K_M on this exact CPU, scaled by active bytes; decode uses
22 GB/s effective. They are predictions to falsify, not results.

| # | model | quant (GB) | engine | key flags | exp. prefill | exp. decode | why this row |
|---|---|---|---|---|---|---|---|
| **R1** | Qwen3.6-35B-A3B | UD-Q4_K_M (22.1) | llama.cpp `ivybridge` | `-fa on -t 12 -b 2048 -ub 512 -c 32768 --reasoning off --no-reasoning-preserve` | **63** (anchor) | **10.9** (anchor) | Control. Must reproduce isis on a dedicated box or the whole matrix is unanchored. |
| **R2** | Qwen3.6-35B-A3B | **UD-IQ4_NL** (18.0) | same | same | 63-85 | 11-13 | §3's central claim. IQ4_NL is on the AVX1 tinyBLAS path and Q4_K_M is not; 4 GB smaller too. Bounded by `mul_mat_id` never calling sgemm — measuring that bound is the point. |
| **R3** | Qwen3-Coder-30B-A3B-Instruct | IQ4_NL or Q4_0 (~17-18) | same + `--cache-reuse 256` | same | 55-75 first turn, **much higher on turns 2..n** | 11-13 | The prefix-cache row. Non-hybrid, so `--cache-reuse` survives startup. Measure *agentic turn 5*, not a cold prompt — that is where it should beat R1/R2 outright. |
| **R4** | Qwen3-Coder-30B-A3B-Instruct | UD-Q4_K_M | same | same | 55-75 | 11-13 | K-quant control for R3. Isolates quant from architecture; without it R3's win is unattributable. |
| **R5** | winner of R1-R4 | winner | same | `-ub` 256/512/1024/2048 x `-t` 12/24, `-fa on/off`, `-ctk f16` vs `q8_0` | — | — | Lever sweep (§4.1, §4.2, §4.4). One model load, `llama-bench`. Expect `-ctk q8_0` to *lose* prefill by disabling tiled FA — that prediction is cheap and worth falsifying. |
| **R6** | gpt-oss-20b | MXFP4 native (~12) | same | `--reasoning-effort low` | 50-70 | 6-10 | Cheapest active bytes and native reasoning-effort control. Attention and `lm_head` stay unquantised in the reference weights, so bytes/token is worse than "20B MoE" suggests. No published agentic coding score — capability is the risk, not speed. |
| **R7** | GLM-4.7-Flash | Q4_K (18.2) | same | `--reasoning on` (card asks for it) | 45-65 | 9-11 | Best τ²-Bench (79.5) and MIT. Risk: MLA up-projection is extra CPU compute at prefill, and llama.cpp's MLA handling on `glm4moe` is unverified here. High capability ceiling, highest engine uncertainty. |
| **R8** | Gemma 4 26B-A4B-it | **official QAT q4_0** (14.4) | same | same as R1 | 55-80 | 8-11 | **Engine probe, not a worker candidate** (SWE-V 17.4). The only official Q4_0 weights in the class, so it is the cleanest measurement of what the AVX1 tinyBLAS path is worth — and a direct comparison against neomindlabs' 16 tok/s ik_llama.cpp prefill on Q8_0. |
| **R9** | Nemotron-3-Nano-30B-A3B | Q4_K_M | same | same as R1 | 40-60 | 9-10 | Only if R1-R7 all miss. Hybrid → same prefix-cache loss as R1/R2; Mamba2 `SSM_SCAN` on CPU is unmeasured here; licence is NVIDIA Open, not Apache/MIT. Two strikes before it starts. |

**Gate to advance past R5:** prefill >= 63 tok/s and decode >= 10.9 on a warm
4-5.5k-token prompt, plus 20 consecutive Claude-Code-shaped tool calls with no
dropped or malformed call. The existing go/no-go in
`research/QWEN36_LOCAL_WORKER_SERVING.md:113-124` is the template; add the
tool-call criterion, which it does not have.

### Commands

Build once per box (or build once, rsync the binaries — identical CPUs):

```
git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp
cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DLLAMA_CURL=ON
cmake --build build -j12 --target llama-server llama-bench llama-quantize
```

Verify the ISA before trusting a single number — `llama-server` prints it at
startup and it must read:

```
system_info: ... AVX = 1 | AVX2 = 0 | FMA = 0 | F16C = 1 | LLAMAFILE = 1 | ...
```

`LLAMAFILE = 1` is the one that matters for §3: it is tinyBLAS. If it reads 0,
every quant falls to untiled `vec_dot` and R2/R3/R8 are meaningless.

Weights:

```
# R1 control (or rsync isis's existing blob rather than re-downloading 21.7 GB)
build/bin/llama-server -hf unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M
# R2
build/bin/llama-server -hf unsloth/Qwen3.6-35B-A3B-GGUF:UD-IQ4_NL
# R3/R4
build/bin/llama-server -hf unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:UD-Q4_K_M
# R7
build/bin/llama-server -hf ggml-org/GLM-4.7-Flash-GGUF:Q4_K
# R8 — official Google QAT Q4_0
build/bin/llama-server -hf google/gemma-4-26B-A4B-it-qat-q4_0-gguf
```

No published Q4_0 exists for Qwen3.6-35B-A3B or Qwen3-Coder-30B-A3B. If R2 shows
a real IQ4_NL win and we want the Q4_0 comparison, requantise **from BF16 or
Q8_0**, never from a K-quant:

```
build/bin/llama-quantize <src-BF16>.gguf Qwen3.6-35B-A3B-Q4_0.gguf Q4_0 12
```

Serving flags, per box (12 cores, 64 GB, dedicated):

```
build/bin/llama-server -m <gguf> --host 0.0.0.0 --port 8095 \
  -t 12 -fa on -b 2048 -ub 512 \
  --parallel 4 --ctx-size 131072 \
  --cache-reuse 256 --reasoning off --no-reasoning-preserve \
  --metrics
```

`--parallel 4 --ctx-size 131072` gives 4 slots x 32k. At 20 KiB/token (R1/R2)
that is 2.6 GiB of KV for all four slots; at 96 KiB/token (R3/R4) it is 12 GiB.
Both fit. Slots are a queueing cap, not a parallel-speed claim — 12 cores are
shared across them (`research/QWEN36_LOCAL_WORKER_SERVING.md:74-76`).

---

## 8. Rows deliberately not in the matrix

| not testing | reason |
|---|---|
| ik_llama.cpp, any model | §2.1: patch unmerged since 2026-07-15, NaN logits reproduced by the maintainer and by a third party on real AVX1 silicon, and 16 tok/s prefill on twice our cores. |
| `--run-time-repack`, or any `*_R4`/`*_R8` repacked GGUF | Reorders into an AVX2-only interleaved layout and garbles output on AVX1 (neomindlabs, 2026-06-08). Unfixed, and out of scope of the AVX1 patch. |
| Ollama runner | §2.3: measured 4-8x slower at prefill on identical hardware, and single-slot for this arch. |
| Any dense model >= 24B | ~15 GB streamed per token → 1.4-1.6 tok/s decode. Qwen3.6-27B has the best SWE-V in the table (75.0) and is still unusable here. |
| Qwen3 8B / 14B dense | Already measured on these boxes: 13.6 and 7.4 tok/s prefill → 5.5 and 10 minute TTFT on a 4.5k prompt. |
| TQ1_0 / TQ2_0 ternary quants | `arch/x86/quants.c` has no `__AVX__` branch for them — AVX2 only. Would run scalar. |
| Q8_0 of a 35B MoE as a *serving* candidate | 36.9 GB and double the bytes/token for a bandwidth-bound decode. Fine as a one-off diagnostic inside R5; not a deployment. |
| KV quantisation (`-ctk/-ctv q8_0`) as a default | §4.4: turns off the tiled CPU FA kernel. We have the RAM; we don't have the prefill. Measured inside R5, not assumed. |
| Speculative decoding / MTP as a headline row | §4.5: a decode lever. Prefill is the constraint. Revisit after a model wins. |
| Gemma 4 26B-A4B as a *worker* | SWE-bench Verified 17.4. Kept as R8 engine probe only. It remains the right judge model per `research/LOCAL_MODEL_STRATEGY.md:204-219` — that decision is unaffected. |

---

## 9. Open questions only the bench can settle

1. **How much of the tiled-GEMM win survives `mul_mat_id`?** §3 proves IQ4_NL/Q4_0
   reach tinyBLAS and Q4_K_M does not, and proves tinyBLAS never sees MoE expert
   matmuls. The residual — attention, dense layers, shared expert, `lm_head` — is
   a minority of FLOPs on an A3B model. R2 minus R1 is the answer. If it is under
   ~10%, §3 is interesting and not actionable.
2. **What does Gated DeltaNet cost at prefill on AVX1?** 30 of Qwen3.6's 40
   layers are linear attention. The DELTA_NET CPU kernels are new and there is an
   open cross-backend correctness bug in the family (#29183, 2026-09-20, SYCL:
   "token soup on long prompts (Qwen3.6, Qwen3.8, Nemotron)"). R1 vs R3 at equal
   quant separates architecture from everything else.
3. **How much does a working prefix cache actually save across a real ticket?**
   Measure turn-5 TTFT, not cold prompt eval. If R3 turns a 70-second per-turn
   prefill into 5 seconds, it beats a model with a 20% better SWE-bench score and
   no cache.
4. **`-t 12` vs `-t 24` for prefill.** SMT theory cuts both ways here and this
   box has never been measured for it. Bench from a shell, not a worker session.
5. **Does the tiled CPU FA kernel actually engage?** The source conditions are
   satisfiable (§4.4) but unobserved. Confirm before trusting the `-ctk`
   prediction.
6. **Does llama.cpp's `glm4moe` path keep MLA compressed or materialise full KV?**
   Decides whether R7's 1.65 GiB/32k estimate holds or is off by 10x.
7. **Real tool-call reliability under the Claude Code CLI's 6 tool schemas.** The
   published bugs (§6) are parser-level and prompt-shape-dependent — #26530 is
   specifically "fails to trigger XML tool calls with large prompts". 20
   consecutive real tool calls is the only test that counts.
8. **Whether LiteLLM forwards `chat_template_kwargs`/`extra_body`.** Decides
   whether thinking control can ever be per-request or must stay per-deployment
   (§5). Answerable at the gateway, without the Macs.

---

## 10. What DDR3-1866 specifically changes

- **Expert size vs cache is the DDR3-specific lever.** All six candidates are
  3-4B active, so they differ less in total bytes than in *access shape*.
  Qwen3.6's experts are tiny (moe_intermediate 512, 8 of 256 routed) — many small
  scattered reads. GLM-4.7-Flash's are 3x larger (1536, 4 of 64) — fewer, longer
  reads. On DDR3 with a 12-core L3, longer contiguous expert reads should convert
  to bandwidth better; the back-solved 22 GB/s effective against ~39 GB/s stream
  (§1) is the size of the prize. This is a real and untested reason R7 might beat
  its conservative estimate.
- **NUMA is not a factor.** Single socket, 12C/24T. `--numa` needs no setting;
  the ik_llama.cpp NUMA mirroring work (#2396) is irrelevant to us.
- **Quad-channel is the whole budget.** 59.7 GB/s peak, ~39 GB/s stream. There is
  no configuration change that raises it — all four DIMM slots are populated and
  DDR3-1866 is the ceiling.
- **F16C is present and load-bearing.** It is what makes f16 KV and the
  `ivybridge` variant cheap, and it is why we get `ivybridge` rather than
  `sandybridge`. Do not build with `-DGGML_F16C=OFF`.

---

## 11. What the same money and watts buy elsewhere

The Mac Pro tier is being wound down, and the honest framing is that this matrix
optimises a platform we are leaving. For scale: a BC-250 blade measured
**181-248 tok/s prefill and 66-87.5 tok/s generation** on gpt-oss-20b MXFP4
(`reports/hapi-blade-coding-model-20260709.md:32`, via
`research/LOCAL_MODEL_STRATEGY.md:365`) — 3-4x our best measured prefill and ~7x
the decode, from a 16 GB unified-memory part that idles far below the Mac Pro's
75 W RAPL cap, on hardware that sells for a fraction of a Mac Pro 6,1's residual
value. Lumen itself (E5-2699 v4, AVX2+FMA, 22 cores, DDR4-2400) already has the
repack GEMM, the IQ panel GEMM and the AVX2 tinyBLAS that §2.4 shows this CPU
compiles out — it should beat these boxes on prefill without buying anything. The
case for spending bench time on the Mac Pros is that they are **already
powered, already racked, and already paid for**, and the matrix above is cheap
to run; it is not a case for keeping them. If R1-R5 land under ~80 tok/s prefill,
the right conclusion is to run the agentic tier on Lumen or a BC-250 blade and
retire the Mac Pro tier to whatever tolerates 10 tok/s.

---

## 12. Sources

Primary, in order of how much weight they carry. All read 2026-09-20.

**Source tree (llama.cpp `master`)** — the ISA and dispatch claims in §2-§4 are
read from source, not inferred:
- `ggml/src/CMakeLists.txt:494-506` — CPU backend variants incl. `ivybridge`
- `ggml/src/ggml-cpu/llamafile/sgemm.cpp:1351,4041-4135` — tinyBLAS AVX1 type dispatch
- `ggml/src/ggml-cpu/arch/x86/quants.c` — per-quant `__AVX__` branches
- `ggml/src/ggml-cpu/arch/x86/repack.cpp` — 8x8 repack GEMM, AVX2+ only
- `ggml/src/ggml-cpu/iqp.cpp:48` — IQ panel GEMM, AVX2+VNNI
- `ggml/src/ggml-cpu/ggml-cpu.c:1306,1389,1550` — sgemm called from `mul_mat`, not `mul_mat_id`
- `ggml/src/ggml-cpu/ops.cpp:8614,8852,9316-9329` — CPU flash attention, tiled-path conditions
- `src/llama-arch.cpp:1075+` — `llm_arch_is_hybrid`, `llm_arch_supports_rs_rollback`
- `tools/server/server-context.cpp:1185-1195,1584` — `cache_reuse` gating, LCP slot selection
- `common/common.cpp:1583-1592` — `common_context_can_seq_rm`, bounded RS rollback
- `common/arg.cpp:1666,1673,3521-3541,3666-3726` — batch/ubatch, chat-template-kwargs, reasoning flags

**Issues and PRs** (repo, number, state as of 2026-09-20):
- ik_llama.cpp #2138 — AVX1 build + MoE fix, **open** since 2026-07-15, NaN logits
- ik_llama.cpp #2192 — vec_dot/vec_dot_type mismatch in non-IQK builds, closed 2026-07-27
- llama.cpp #17966 — Ivy Bridge/Piledriver CPU backends, closed 2025-12-28 (landed)
- llama.cpp #26994 — prompt cache never hits on hybrid SSM (Qwen3.6), **open** 2026-08-13
- llama.cpp #26817 — temp-0 tool calling nondeterministic on hybrid Qwen3.6-A3B, **open** 2026-08-09
- llama.cpp #27767 — `tool_choice` not enforced with `enable_thinking=false`, **open** 2026-08-26
- llama.cpp #26987 — Qwen3-Coder lazy tool-call trigger never fires, **open** 2026-08-12
- llama.cpp #26530 — Qwen3-Coder template fails to trigger XML tool calls with large prompts, closed 2026-08-03
- llama.cpp #19553 — gpt-oss tool-call double-escaping, **open** 2026-02-12
- llama.cpp #27221 — `reasoning_effort` to control `enable_thinking`, **open** 2026-08-17
- llama.cpp #22336 — per-request `enable_thinking` via `extra_body`, **open** 2026-04-24
- llama.cpp #29183 — DeltaNet token soup on long prompts (SYCL), **open** 2026-09-20
- llama.cpp #23247 — schema-constrained decoding for Gemma 4 tool calls, **open** 2026-05-18

**Model cards** (HuggingFace, `config.json` + README):
`Qwen/Qwen3.6-35B-A3B`, `Qwen/Qwen3-Coder-30B-A3B-Instruct`, `zai-org/GLM-4.7-Flash`,
`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16`, `openai/gpt-oss-20b`,
`google/gemma-4-26B-A4B-it`, `google/gemma-4-26B-A4B-it-qat-q4_0-gguf`,
`unsloth/Qwen3.6-35B-A3B-GGUF`, `ggml-org/GLM-4.7-Flash-GGUF`.

**Community:** Ryan Findley / Neomind Labs, "Running Gemma 4 26B at 5 tokens/sec
on a 13-year-old Xeon with no GPU", 2026-06-08 —
`https://www.neomindlabs.com/2026/06/08/running-gemma-4-26b-at-5-tokens-sec-on-a-13-year-old-xeon-with-no-gpu/`.
Dual Xeon E5-2690 v2, AVX1, DDR3, Gemma 4 26B-A4B Q8_0, patched ik_llama.cpp with
`GGML_USE_IQK_MULMAT` off: ~5.2 tok/s decode, ~16 tok/s prompt eval; and the
`--run-time-repack` / `Q8_0_R8` corruption finding.

**In-repo:** `research/LOCAL_MODEL_STRATEGY.md` (§2 hardware table, §3 arithmetic,
§4 prefill-is-the-bottleneck, line 365 BC-250 numbers),
`research/QWEN36_LOCAL_WORKER_SERVING.md` (measured isis/hathor/nephthys table,
slot plan, go/no-go template), `kb/ISSUE-LOG.md` row 315 (never bench from a
worker session).
