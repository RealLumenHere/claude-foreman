# wq#4426 — vLLM on RTX 3090: does it beat Ollama for fleet inference?

Date 2026-10-19 · worker `fworker-wq4426` · extends `docs/hardware-tiers-spec.md` §Tier 5
(`docs/hardware-tiers-spec.md:140-174`) and `research/LOCAL_MODEL_STRATEGY.md`.

Research-only ticket (no RTX 3090 is in fleet inventory yet — confirmed below), so this
is a desk evaluation to pre-stage the decision for when the hardware lands, not a
benchmark. Live-search doctrine ran first per CLAUDE.md; findings below are tagged
`[WEB <url>, <date>]` where sourced, `[LOCAL-VERIFIED <how>]` where checked against this
repo, `[UNRESOLVED]` where the question needs the physical card.

## 0. Fleet state check (is this even actionable yet?)

No discrete GPU is present in current fleet inventory. `research/INVENTORY_RECONCILIATION.md:83-84`
and `:343` reference "3090" only as the Dell OptiPlex 3090 UFF chassis model (host `anubis`,
an i5-1145G7 iGPU box) — not an RTX 3090 GPU. `research/LOCAL_MODEL_STRATEGY.md` (the
fleet's current model-placement doc, Sept 2026) has zero GPU/VRAM/CUDA mentions: every rung
it plans is CPU inference on AVX2/AVX-512 hosts. [LOCAL-VERIFIED grep -i "3090\|gpu\|vram"
across `research/`, `docs/hardware-tiers-spec.md:140-174` Tier 5 is written as a
forward-looking spec, not a deployed config]

So: the ticket's own framing ("evaluate when GPUs arrive") is correct — there is nothing to
benchmark today. This doc is the engine-choice recommendation to execute the day the 3090s
arrive, queued as the physical-task dependency (buying/installing the cards is Frank's
hands, not something this worker can do — instinct 3).

## 1. Verdict

**Recommend vLLM over Ollama once 2x RTX 3090 are in the fleet, but only for the
single-model, high-concurrency serving role** (e.g. a shared completion endpoint multiple
agents hit at once). Keep Ollama for the low-concurrency, swap-heavy, many-small-models role
the fleet actually runs today (`OLLAMA_MAX_LOADED_MODELS`, model-per-tier swapping per
`docs/hardware-tiers-spec.md:50,73,96,121`) — vLLM's win is batched throughput under
concurrent load, not model-swap convenience, and the fleet's current load profile
(sequential single-agent calls across many small models) doesn't yet create the batching
pressure vLLM is built to exploit.

Hardware compatibility is not a blocker: RTX 3090 (Ampere, SM/CC 8.6) clears vLLM's minimum
supported compute capability (Volta, CC 7.0+; Ampere CC 8.0+ is the recommended floor)
[WEB learn.microsoft.com/.../foundry-local/reference-models, 2026, Foundry Local vLLM GPU
reference table]. The open question is magnitude of the win on *this* fleet's workload, which
needs the physical cards — flagged `[UNRESOLVED]` below rather than guessed.

## 2. What changed in vLLM (the "new updates" the ticket flags)

- **v0.29.0 shipped a security fix**: CVE-2026-100648, an audio-decoding DoS in versions
  before 0.29.0 [WEB sentinelone.com/vulnerability-database/cve-2026-100648, 2026-10-01].
  Pin to >=0.29.0 when this gets built, not because of the 3090 evaluation but because an
  older pin would ship a known DoS.
- **TurboQuant KV-cache compression**: ~6x KV-cache memory reduction at held accuracy on
  needle-in-a-haystack evals, landed as a vLLM integration [WEB morphllm.com/turboquant,
  2026-10-07]. This is the feature most relevant to a 24GB card: KV cache is what caps
  context length and concurrent-request count on a single 3090, so a 6x cut directly raises
  how many concurrent requests or how long a context the card can hold before PagedAttention
  has to evict.
- **Continuous batching + PagedAttention** (the ticket's named features) are not new in
  0.29 — they're vLLM's original 2023 contribution and the reason it outperforms
  request-at-a-time serving under concurrent load: new requests join an in-flight batch
  as GPU slots free up, and PagedAttention pages the KV cache like OS virtual memory instead
  of reserving a worst-case contiguous block per request [WEB sitepoint.com/ollama-vs-vllm-
  performance-benchmark-2026, 2026-03-05; agenticwire.news/article/vllm-vs-ollama,
  2026-09-29]. The ticket's "vLLM tweet" reference wasn't locatable as a specific post via
  fleet-search (SearXNG's X/Twitter coverage is poor); the vllm.ai blog's most recent post
  as of this search is the Vera Rubin NVL72 datacenter preview
  [WEB vllm.ai/blog/2026-10-09-vera-rubin-preview, 2026-10-09], which is not relevant to a
  2x3090 desktop box — noting this as `[UNRESOLVED]` rather than fabricating a tweet URL
  (instinct 8: an invented citation here would be evidence of fabrication, not of a feature).
- **Ollama's own concurrency path has NOT closed this gap on CUDA.** The open serialization
  issue on Ollama's roadmap is specifically about the experimental MLX (Mac) runner, not the
  llama.cpp/CUDA path Ollama uses on NVIDIA GPUs
  [WEB github.com/ollama/ollama/issues/17666]. The CUDA path already has
  `OLLAMA_NUM_PARALLEL`, which this fleet's own spec sets to `1` on every tier
  (`docs/hardware-tiers-spec.md:98,123`) — i.e. the fleet currently runs Ollama in
  single-request mode by its own config, which is the mode where vLLM's batching advantage
  doesn't show up at all. Raising `OLLAMA_NUM_PARALLEL` is the cheap A/B to run before
  standing up vLLM (see §4).

## 3. Published throughput deltas (single 3090, not fleet-measured)

- Cross-GPU benchmark table lists RTX 3090 at ~50 tok/s on Qwen2.5-7B, competitive with much
  newer 48GB cards on a per-request basis [WEB runpod.io/articles/guides/best-gpu-for-llm-
  inference]. This is engine-agnostic single-stream throughput — it says the 3090 itself is
  fine, not which server wins.
- A published vLLM-vs-llama.cpp-style comparison (engine swap, same model family) frames
  vLLM's dual-3090 win as "the engine change is the big win, NVLink is the smaller one on
  top" [WEB openclawdc.com/blog/vllm-tensor-parallel-dual-gpu-setup, 2026-09-10].
- A hands-on writeup reports **30 → 60 tok/s** moving a workload from a single-GPU/naive
  setup to vLLM with `--tensor-parallel-size 2` across two 3090s
  [WEB xhinker.medium.com/.../from-30-to-60-tokens-second-how-i-got-vllm-running-on-2x-rtx-
  3090, 2026-05-05]. 2x is a single blog's number for one model/config, not a fleet-general
  constant — treat as a plausibility bound, not a spec number.
- NVLink detail for the build-when-arrived step: **RTX 3090 is the last consumer NVIDIA card
  with an NVLink bridge connector** (used bridges ~$80-150); it roughly halves the
  tensor-parallel sync penalty versus PCIe-only, but is "worth it" rather than required — TP
  works over PCIe too, just with a larger comms tax [WEB llmconfigurator.com/en/guides/
  multi-gpu-llm-inference, 2026-07-11].

## 4. What to actually run when the cards land (the acceptance test for this recommendation)

In order, cheapest-to-most-expensive to falsify:
1. Baseline Ollama at `OLLAMA_NUM_PARALLEL=2` (not the fleet default of 1) under N concurrent
   requests against the general-rung model — this isolates "did we leave an Ollama knob on
   the table" from "does vLLM actually win."
2. Same model, same concurrency, vLLM single-GPU (`--tensor-parallel-size 1`) — isolates the
   engine effect from the multi-GPU effect.
3. vLLM `--tensor-parallel-size 2` across both 3090s, with and without an NVLink bridge if
   one gets bought — isolates the TP-communication effect.
4. Repeat at the fleet's actual concurrency pattern (many small agent calls, not a synthetic
   load generator) — ticket context and `research/LOCAL_MODEL_STRATEGY.md` both suggest the
   fleet's real traffic may not hit the concurrency vLLM needs to pay off; measuring on
   synthetic load alone would be the wq#2630 "probe that can't fail" trap (confirms a
   batching win that the fleet's actual request pattern never exercises).

This step needs the physical GPUs; it is `[UNRESOLVED]` until they're racked, and is the one
genuinely Frank-gated item here (buying/installing hardware — instinct 3's "physical act
nobody else can perform"), queued as a physical task, not a blocking question.
