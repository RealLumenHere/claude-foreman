# wq#3835: AMD CPU tier research — Zen 1 through Zen 5, APU ROCm viability, BC-250 placement

Goal: give the Limpet installer an AMD-side tier ladder equivalent to the existing
per-box Intel docs (`research/WQ3798_M80S_LLM_MODEL_SELECTION.md`'s i7-12700
AVX2+AVX-VNNI/DDR4 characterization, `research/MAC_CPU_MODEL_SHORTLIST.md`'s
no-AVX2/DDR3 baseline). Those docs don't use a single shared ladder file; each box gets
its own AVX-capability + memory-bandwidth characterization, and the tier falls out of
that. This doc does the same thing for AMD desktop/APU/Threadripper parts.

## Sourcing note: research-council ran 1/3 seats, not 3/3

The ticket asked to send this to Gemini+DeepSeek+ChatGPT via `research-council`
(`tools/council/council.py`, seats `gemini-flash` / `deepseek-v4-pro` /
`cerebras-oss-120b` standing in for GPT per `tools/council/council.yaml`). Ran it twice
(`/tmp/fworker-wq3835/council-out/council-20261014T023133-2408173.json` and
`...T023505-2418910.json`), both times **gemini-flash 429'd** ("exceeded your current
quota") and **cerebras-oss-120b 402'd** ("Payment required to access this resource")
— account-level outages, not request-shaped, so a third retry would not have helped.
Logged as `kb/ISSUE-LOG.md` row 1296. `deepseek-v4-pro` answered cleanly both times
(93s / 181s); its answer is quoted and attributed per-question below, graded by its own
stated confidence. `fleet-search web`/`reddit`/`github` were also tried first per the
prior-art doctrine and are independently dead right now (`fleet-search web` → all four
general engines suspended/no `BRAVE_API_KEY`, wq#2595, still open; `reddit` backend
returns `ok:true` with empty results even for a bare `"llama.cpp"` query, consistent
with the already-logged wq#2568/wq#2601 "empty-result cache on partial outage" bug;
`github` → 401, no usable token) — not re-logged since all three are already tracked.
Net: this doc is DeepSeek's reasoning-from-known-architecture (explicitly flagged
per-item as measured vs. estimated) plus the fleet's own measured BC-250 data below,
not a 3-model consensus. Re-run `research-council` once Gemini quota resets or Cerebras
billing is funded to get the other two opinions.

## Fleet-internal ground truth used (not estimated)

- **BC-250 (Hapi blades) is Zen 2, not a mystery part**: "AMD Cyan Skillfish APU: Zen 2,
  6C/12T per blade ... 16GB GDDR6 UNIFIED per blade, shared CPU+GPU"
  (`research/fleet_hardware.json` node `hapi`).
- **ROCm is confirmed dead on BC-250's gfx1013, fleet-wide note, not a guess**: "No ROCm
  targets ... BC-250 gfx1013 is NOT a ROCm target either (bc250.training_and_autoevolve_
  assessment: HSA_OVERRIDE/hipBLAS path assessed REJECTED/non-functional)"
  (`research/fleet_hardware.json`, `hardware_capabilities.rocm_note`). Independently
  confirmed in `/home/lumen/agents/work-queue/reports/bc250-llamacpp-vulkan-rocm-20260709.md`:
  Vulkan (RADV) is the only usable GPU compute path on gfx1013; AMD never shipped
  rocBLAS/Tensile solution libraries for it, and faking `gfx1030` via
  `HSA_OVERRIDE_GFX_VERSION` is unsafe (GFX10.1 vs 10.3 ISA mismatch → silent compute
  errors).
- **BC-250 measured throughput**: 66-87.5 tok/s gen / 181-248 tok/s prefill on
  `gpt-oss-20b` (MXFP4, one blade) and ~37.5 tok/s on a 35B-A3B-class MoE at Q4, 64K ctx
  (`research/LOCAL_MODEL_STRATEGY.md:365-366`,
  `/home/lumen/agents/work-queue/reports/hapi-blade-coding-model-20260709.md`).
- **EPYC-class memory-bandwidth-bound behavior** (adjacent evidence for the
  "bandwidth, not cores" claim running through this doc): "The single most important
  spec is memory bandwidth, not core count. MoE CPU inference is bandwidth-bound ... a
  mid-core Genoa (48c) with 12 channels filled beats a higher-core part with half the
  channels populated" (`/home/lumen/agents/work-queue/reports/wq109_zendnn_horus_2026-07-13.md`).

## Tier ladder (summary)

| Tier | Parts | AVX | Mem config (typical) | 7B Q4 tok/s | Verdict |
|---|---|---|---|---|---|
| T0 — not recommended | FX/Bulldozer family (AM3+) | AVX, no AVX2 | DDR3-1866/2133 dual-ch (~30 GB/s) | ~1-2 (DeepSeek, medium conf.) | drop from the installer's supported list, same as the Mac Pro 6,1's no-AVX2 baseline |
| T1 — usable, slow | Zen 1 (Ryzen 1000) | AVX2 (128-bit native FPU) | DDR4-3200 dual-ch | ~10-17 (DeepSeek, moderate-high conf.) | works, but closer to T2's floor than its ceiling |
| T2 — baseline desktop | Zen 2 (Ryzen 3000) | AVX2 | DDR4-3200 dual-ch | ~12-17 | solid 7B/13B tier |
| T3 — mid desktop | Zen 3 (Ryzen 5000) | AVX2 | DDR4-3600 dual-ch | ~16-22 | incremental over T2 |
| T4 — high desktop | Zen 4 (Ryzen 7000) | AVX2 + AVX-512 (double-pumped) | DDR5-6000 dual-ch | ~25-35 | biggest generational jump, mostly DDR5 |
| T4-APU | Ryzen 7040/8040 (RDNA3 iGPU) | AVX2/AVX-512 (CPU side) | DDR5, unified-ish | CPU tier same as T4; GPU offload Vulkan-only | do not promise ROCm in the installer |
| T5 — HEDT | Threadripper / PRO (quad/8-ch) | per-gen | quad-ch DDR4/DDR5, PRO 8-ch | ~1.8-2x (quad) / ~3.5-4x (8-ch) over T2-T4 dual-ch, NUMA-tuned | scales close to channel count if `numactl --interleave`/llama.cpp NUMA is used |
| Special — unified-GDDR6 APU | BC-250 (Cyan Skillfish, Zen 2 + gfx1013) | n/a (GPU path) | 16GB GDDR6 unified, Vulkan-only | 37.5-87 tok/s (fleet-measured) | tier by GPU bandwidth, not by its Zen 2 CPU — see §8 |

All non-BC-250 tok/s figures above are DeepSeek's answer (quoted per-question below,
confidence as DeepSeek stated it), not independently measured on this fleet. Treat the
numbers as installer-default *expectations*, not guarantees, until a real box is
benchmarked.

## 1. FX/Bulldozer family — AVX but no AVX2

> Tok/s roughly 0.5-2.5 for 7B Q4; not recommended for interactive use. llama.cpp's CPU
> matmul kernels are heavily optimised for AVX2 (256-bit); without it, Bulldozer falls
> back to 128-bit AVX/SSE, roughly halving theoretical compute throughput vs. a
> Haswell-era Intel chip with AVX2. But 7B Q4 generation is memory-bound first: FX-8350's
> DDR3-1866 dual-channel gives only ~30 GB/s, so an AVX2-equipped Intel chip at similar
> bandwidth lands ~3-5 tok/s while FX lands ~1-2 tok/s. Confidence: medium (community
> reports, not DeepSeek's own measurements).
> — DeepSeek-v4-pro via research-council, 2026-10-14

This matches the same shape as the Mac Pro 6,1 baseline in
`research/WQ3798_M80S_LLM_MODEL_SELECTION.md:3-9` (no-AVX2 parts are compute-bound on
prefill even when generation is bandwidth-bound) — FX should get the same "not
recommended" treatment the Mac doc implies for pre-AVX2 silicon, not a usable tier.

## 2. Zen 1 Ryzen 1000 — AVX2 but 128-bit-wide FPU

> The halved FPU throughput does **not** halve tok/s; memory bandwidth dominates. At
> equal memory config (dual-channel DDR4-3200) Zen 2 is only ~10-20% faster than Zen 1,
> not 2x. Token generation is bandwidth-bound (bytes/token ≈ model size); Zen 1's
> 128-bit-AVX2-uop-splitting can still saturate available bandwidth for 7B/13B Q4
> models. The gen-to-gen difference is mostly larger L3 cache, memory-controller
> latency, and IPC, not FPU width. Confidence: moderate-high.
> — DeepSeek-v4-pro via research-council, 2026-10-14

Answers the ticket's framing directly: **no, it does not halve throughput** — the 256-
bit-vs-128-bit FPU question is a compute-side concern, and decode is bandwidth-bound,
so Zen 1 belongs one notch under Zen 2/3/4 (T1 above T2), not a separate half-speed
tier.

## 3. Zen 2 vs Zen 3 vs Zen 4 — practical tok/s for 7B/13-14B

> 7B Q4 (~4.5GB): Zen 2 (DDR4-3200) 12-17 tok/s, Zen 3 (DDR4-3600) 16-22 tok/s, Zen 4
> (DDR5-6000) 25-35 tok/s. 13B Q4 (~7.5GB): Zen 2 7-10, Zen 3 9-13, Zen 4 15-20.
> tok/s ≈ (effective bandwidth ÷ model size) × efficiency(0.6-0.8). DDR4-3200 dual-ch
> ~51 GB/s theoretical, DDR5-6000 ~96 GB/s. Most of the generational gain is DDR5
> bandwidth, not IPC/AVX-512 — Zen 4's AVX-512 (double-pumped 256-bit) is estimated to
> add only ~10-15% over Zen 3 at equal memory speed, not isolated in a controlled test.
> Confidence: medium (interpolated from community benchmarks).
> — DeepSeek-v4-pro via research-council, 2026-10-14

## 4. DDR4 vs DDR5 bandwidth impact per generation

> Holding CPU generation fixed, DDR4-3200→DDR5-6000 gives a ~1.7-1.9x tok/s multiplier,
> essentially proportional to the bandwidth ratio (51.2 → 96 GB/s = 1.875x theoretical).
> The actual Zen 3→Zen 4 uplift seen is ~1.5-1.8x because Zen 4 also improves the memory
> controller, not pure DDR4-vs-DDR5. Confidence: high (bandwidth-bound scaling is
> well-established for llama.cpp CPU decode).
> — DeepSeek-v4-pro via research-council, 2026-10-14

## 5. Infinity Fabric speed — does it bottleneck inference bandwidth?

> Not a bottleneck for single-CCD desktop Ryzen when FCLK:MCLK is 1:1. IF read-port
> width is 32 bytes/FCLK cycle (Zen 2/3); at DDR4-3200 (FCLK 1600MHz) that's 51.2 GB/s,
> exactly matching dual-channel DDR4 — it isn't the limiter. DDR5-6000 (Zen 4, FCLK
> ~2000MHz) gives ~64 GB/s IF read bandwidth, still ≥ effective memory bandwidth. This
> is sequential-read-bound inference, not the fine-grained-latency-sensitive workload
> (gaming) where IF is known to matter. Multi-CCD Threadripper can hit NUMA effects,
> mitigated with `numactl`. Confidence: high (AMD IF architecture disclosures + bandwidth
> arithmetic).
> — DeepSeek-v4-pro via research-council, 2026-10-14

So IF matters only indirectly: it gates whether FCLK can stay 1:1 with faster memory
(the well-known "FCLK wall" above DDR4-3800ish on Zen 2/3), which caps *memory*
bandwidth, not inference bandwidth on top of memory bandwidth.

## 6. AMD APUs with RDNA iGPUs — can Ollama use ROCm?

> ROCm is not a reliable path for consumer APUs; Vulkan (RADV/AMDVLK) is the dependable
> backend for Ollama/llama.cpp GPU offload today. AMD's official ROCm/rocBLAS fat
> binaries ship precompiled Tensile libraries only for certain discrete GPUs (gfx906,
> gfx908, gfx90a, gfx1030, gfx1100, etc.) — consumer APU targets like gfx1103 (Phoenix,
> Ryzen 7040) or gfx1102 (Hawk Point, Ryzen 8040) are not in the default rocBLAS fat
> binaries despite sharing the RDNA3 ISA. `HSA_OVERRIDE_GFX_VERSION=11.0.0`/`10.3.0`
> overrides can sometimes force a mismatched code object to load, but stability/perf are
> poor and crashes/CPU-fallback are commonly reported. Effectively all consumer
> RDNA2/RDNA3 APUs (Phoenix, Hawk Point, Strix Point) are Vulkan-only in practice unless
> experimenting with unsupported overrides; Strix Halo may get official ROCm support
> eventually but doesn't have it now. Confidence: very high on the reliability split
> (widely-held r/LocalLLaMA + ROCm-repo-issue consensus); the exact unsupported-gfx list
> is the less-certain part.
> — DeepSeek-v4-pro via research-council, 2026-10-14

This is the same split the fleet already lives with on BC-250's older gfx1013 (§
"Fleet-internal ground truth" above): **no official ROCm support, Vulkan is the
production path**, and that pattern generalizes forward to the newer Phoenix/Hawk Point
APUs the ticket names (Ryzen 7940HS/8945HS-class). The Limpet installer should default
every RDNA2/RDNA3 APU to the Vulkan backend and not offer a ROCm option for them.

## 7. Threadripper quad-channel — how much does 4-channel help?

> Dual→quad-channel uplift is close to linear (~1.8-2.0x); dual→8-channel (Threadripper
> PRO) ~3.5-4.0x, provided NUMA is handled. LLM batch-1 decode is bandwidth-bound, so
> throughput scales with channel count; Threadripper PRO 8-channel DDR4-3200 can reach
> ~180 GB/s theoretical, and with `numactl --interleave`/llama.cpp NUMA support, real 7B
> Q4 throughput can exceed 50 tok/s vs. ~25-30 tok/s on dual-channel Zen 4. NUMA
> remote-access overhead is modest (≤15% loss) if tuned; example: Threadripper 3970X
> (quad-ch) ~35-45 tok/s on 7B Q4 vs. ~18-22 tok/s on dual-channel Ryzen 9 3950X.
> Confidence: medium-high (r/LocalLLaMA + EPYC-adjacent server benchmarks).
> — DeepSeek-v4-pro via research-council, 2026-10-14

Consistent with the fleet's own EPYC finding that channel count, not core count, is the
lever for CPU-bound LLM inference (`wq109_zendnn_horus` quote above) — Threadripper is
the same architecture family as the EPYC parts that report already covers.

## 8. BC-250 (Cyan Skillfish) tier placement

> With GDDR6 unified memory and Vulkan offload, BC-250 outperforms all desktop
> DDR4/DDR5 CPU tiers and behaves more like a small dedicated GPU (~GTX 1650-class); it
> should sit in a hardware-accelerated tier, not a desktop-CPU tier. The reported
> 37.5-87 tok/s range implies effective bandwidth of ~300-400 GB/s (87 tok/s × ~4GB/tok
> ≈ 350 GB/s estimate) — 5-8x a dual-channel DDR4-3200 system. The Zen 2 CPU cores
> (6-core, modest clock) are not the constraint; it's memory-bound on the GPU path.
> Confidence: high for the ranking conclusion (the tok/s figures are fleet-provided, not
> DeepSeek's own measurement; the bandwidth figure is DeepSeek's extrapolation from
> them).
> — DeepSeek-v4-pro via research-council, 2026-10-14

Matches the fleet's own framing exactly: the Hapi/BC-250 entry in
`research/fleet_hardware.json` notes its core-unlock tool gave "zero inference gain,"
i.e. the fleet already independently found the CPU side is not the bottleneck. **Tier
placement: do not slot BC-250 by its Zen 2 CPU spec at all — place it with Vulkan-GPU-
offload boxes (alongside `nephthys`'s FirePro D500 Vulkan tier,
`research/fleet_hardware.json:30`), gated on "Vulkan backend available," not on CPU
generation.**

## What the installer should actually encode

1. Gate on AVX2 presence first (FX/Bulldozer and earlier → unsupported/"not
   recommended," same treatment as the Mac Pro 6,1 no-AVX2 baseline).
2. Within AVX2 parts, bucket by **DDR generation + channel count**, not Zen generation
   per se — Zen 1 vs Zen 2 at equal DDR4-3200 dual-channel is a ~10-20% gap (§2), far
   smaller than DDR4→DDR5 (~1.7-1.9x, §4) or dual→quad-channel (~1.8-2x, §7).
3. For any RDNA2/RDNA3 APU (Phoenix 7040, Hawk Point 8040, and BC-250's older gfx1013),
   default to the Vulkan backend and do not expose a ROCm toggle (§6, §8) — this mirrors
   the fleet's own `rocm: false` / Vulkan-only posture in `research/fleet_hardware.json`.
4. Thread/NUMA-aware builds matter specifically for Threadripper/EPYC-class multi-CCD
   parts (§5, §7); default single-socket desktop installs don't need NUMA tuning.

## Open items

- All non-BC-250 numbers above are DeepSeek's reasoning from known architecture +
  community benchmarks, not fleet measurements — re-verify against an actual FX, Zen 1,
  and Zen 4 box before the installer ships a numeric tok/s promise, same caveat
  `research/WQ3798_M80S_LLM_MODEL_SELECTION.md:58` already states for its own numbers.
- Re-run `research-council` for the Gemini/GPT seats once `kb/ISSUE-LOG.md` row 1296's
  quota/billing outage clears, to get a real 3-model cross-check instead of one seat.
- File the actual Limpet-installer tier-config change (code, not research) as a
  follow-up ticket that cites this doc, the way
  `research/WQ3798_M80S_LLM_MODEL_SELECTION.md` was itself research-only.
