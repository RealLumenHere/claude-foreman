# wq#3799 — verify: wq#3798 M80s LLM model selection

**Verdict: CONFIRM.** The doc's model recommendations and every checkable citation hold up
under an adversarial fact-check. One imprecision found (overstates "measured" where the
underlying claim is a design consensus, not a benchmark) — noted below, not load-bearing
enough to contradict the deliverable.

Verifier: claude-sonnet-5@high, fresh instance, archetype 8 verify-pass on PR
`claude-foreman#1796` (merged `9a2ed9a3`, `research/WQ3798_M80S_LLM_MODEL_SELECTION.md`,
102 lines, docs-only — no code, no automated probe exists for this ticket type). Read-only:
no files edited, no git state touched, no ssh.

## What I checked

This is a research/recommendation doc, not code, so "run its probe" means fact-checking
every checkable claim against the repo and live sources rather than executing a test suite.

### Repo citations — all verified exact

- `research/models.yaml:247-248`: `gemini-3-flash` is `status: DARK`, gated on wq#2627
  (Frank's paid AI Studio key). Matches the doc's claim exactly.
- `kb/ISSUE-LOG.md:1432` (row 1283): text matches the doc's "Why not a live Gemini/DeepSeek
  completion call" section verbatim in substance (gemini DARK, deepseek key in guard-denied
  secrets dir, fleet-search substituted).
- `kb/ISSUE-LOG.md:1108`: confirms `/home/lumen/.secrets` is a `guard.sh` denied path under
  wq#616 — same mechanism the doc cites for the deepseek-v4-flash key being unreachable.
- `ops/wq3658-hardware-role-assignment-limpet-secondar/ROLE_ASSIGNMENT.md:91-96`: confirms
  M80s's physical/privileged provisioning (wq#3703 MEBx/AMT, wq#3704 MeshCommander, wq#3705
  user/NVMe/unit install) has not landed, matching the doc's stated reason for not adding an
  `inference_nodes.yaml` node.
- `research/inference_nodes.yaml`: grepped for `m80s` — no match, confirming the doc's "What
  this doc does NOT do" claim that no node was added.
- `research/inference_nodes.yaml:70,107,136`: `served_ctx` is a real field, matching the
  doc's "same discipline as `served_ctx`" context-sizing reference.

### Model facts — all verified against live sources (fleet-search web/read, SearXNG)

- **Qwen3-Coder-Next** (80B/3B-active, Apache 2.0): confirmed real
  (huggingface.co/Qwen/Qwen3-Coder-Next, lmstudio.ai, ollama.com). Unsloth/Ollama/GitHub
  sources converge on ~46GB RAM/VRAM for 4-bit — matches the doc's "Q4 GGUF ≈ 46GB" exactly.
- **Nemotron 3 Super 120B-A12B** (NVIDIA Nemotron Open Model License): confirmed real
  (build.nvidia.com modelcard, NGC catalog, OpenRouter). Unsloth's own run guide
  (unsloth.ai/docs/models/nemotron-3/nemotron-3-super) states verbatim: "The 4-bit versions
  of the model requires ~64GB RAM - 72GB RAM" and "1M-token context window" — matches the
  doc's "Q4 GGUF ≈ 64-72GB" and "1M context" exactly, and independently corroborates the
  pinggy.io figure the doc cites as its source.
- **Qwen3.6-35B-A3B** (35B total/3B active, Apache 2.0): confirmed real, released April 2026
  (huggingface.co/Qwen/Qwen3.6-35B-A3B, qwen.ai blog, NVIDIA NGC). Matches the doc's shape
  claim exactly.
- **Qwen3.8-27B** (dense, 262K context, Apache 2.0): confirmed real, released Aug 2026
  (huggingface.co/Qwen/Qwen3.8-27B, Qwen's own X announcement, mlq.ai). Matches the doc's
  size/context/license claims exactly.
- **DDR4-3200 dual-channel ≈ 50 GB/s**: 3200 MT/s × 8 bytes × 2 channels = 51.2 GB/s.
  Arithmetically correct.
- **AVX2/AVX-VNNI prefill speedup "30-500% faster"**: justine.lol/matmul benchmark deltas
  found via a third-party citation-audit source (cpuperf.com/evidence/record) show "810
  gigaflops" vs "MKL... 295 gigaflops" (≈175% faster) and "63 prompt tok/sec" (llamafile)
  vs "40" (stock llama.cpp) (≈57.5% faster) on the same justine.lol source the doc cites.
  Both fall inside the claimed range; not fabricated, though the range is a loose paraphrase
  of a small number of data points rather than a tight benchmark suite.

### One imprecision found — not load-bearing

The doc says (research/WQ3798_M80S_LLM_MODEL_SELECTION.md:27-30): "the 35B-A3B MoE was
pulled from isis specifically because *prefill*, not decode, was **the measured bottleneck**
on no-AVX2 hardware." This overstates the evidentiary basis:

- `research/WQ3794_MAC_FLEET_RECONFIG.md:10-11` (the cited source) says only "the MoE-prefill
  problem all three reviews in the research doc agree on" — a consensus among advisory
  reviews, not a benchmark result.
- `scratch/research/mac-llm-recommendations.md:260` (one of those three reviews) hedges
  further: the MoE's "expert routing and memory behavior **can produce** poor prompt
  throughput on this CPU" — a qualitative prediction, not a measurement.
- `reports/3795_judgment_wq3794_hathor_prefill_tps_premature_clear.md:28-29`, an opus-5@high
  judgment on the same wq#3794 PR, confirms isis is `state: down` in
  `inference_nodes.yaml:113` and `status: down` in `models.yaml:330` — i.e. isis never
  served live traffic with the 35B-A3B blob, so no prefill-vs-decode number was ever
  actually measured on it. `models.yaml:362-369` (wq#2583) separately records 0/166 local
  fworker sessions ever succeeding on `local-worker`.

So "the measured bottleneck" should read "the MoE-prefill problem reviewers agreed would be
a bottleneck" — a planning judgment, not a recorded number. This doesn't change the doc's
recommendation (AVX2/AVX-VNNI genuinely accelerates int8 GEMM prefill vs scalar/AVX1 per the
justine.lol evidence above, independent of whether isis's specific case was ever benchmarked),
and the doc elsewhere is appropriately hedged ("Needs direct benchmarking before being
trusted as a number, not a planning estimate"). Noting it as a correction for whoever reads
this doc next, not as grounds to contradict.

## Why CONFIRM and not CONTRADICT

The deliverable is the model picks (Qwen3-Coder-Next primary, Nemotron 3 Super on-demand
with an explicit RAM caveat and a fallback, Qwen3.6-35B-A3B fallback) plus the decision to
defer `inference_nodes.yaml` registration. Every checkable fact behind those picks —
model existence, parameter counts, quant sizes, context windows, licenses, repo citations,
and the decision not to guess a node config — held up against independent live sources and
repo state. The one inaccuracy found is in supporting rhetoric about *why* MoE prefill
matters, not in the recommendation itself, and the doc's own hedging already protects a
reader from treating any of this as a measured number.

## What I did not check

- Did not re-run or request a live Gemini/DeepSeek completion (same blockers logged in
  ISSUE-LOG #1283 apply to this session too; re-verifying that block wasn't useful).
- Did not benchmark any model on M80s hardware — M80s has no OS/NVMe/unit installed yet
  (wq#3703/3704/3705 pending), so there is nothing to benchmark against.
- Did not fetch justine.lol/matmul directly (TLS cert on that site is expired); relied on a
  third-party citation-audit mirror (cpuperf.com) that quotes the specific figures verbatim
  with source attribution.
