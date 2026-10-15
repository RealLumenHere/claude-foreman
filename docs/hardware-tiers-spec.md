# Hardware Tiers Specification

> Limpet Project — CPU/GPU classification, model selection, performance profiles
>
> Status: DRAFT v0.2 — 2026-10-15
> Author: Claude (Limpet Architect, wq#3806 draft; AMD section wq#3919/wq#3939)
> Ticket: wq#3806, wq#3919, wq#3939

This is the git-tracked copy of the hardware tiers spec. It supersedes the draft that
was being edited at `/home/lumen/projects/limpet/docs/hardware-tiers-spec.md`
(outside any git repo — flagged `[NO-ARTIFACT]` in wq#3919's done notes). Section 3
below ports wq#3919's AMD research pass verbatim where it was evidence-backed, and
closes the two items wq#3939 was asked to add that the wq#3919 draft did not cover:
the AMD-side detection algorithm (§3.9 — no P/E split, CCD topology instead) and
formal advanced-settings exposure for Threadripper/EPYC power users (§3.10).

---

## 1. Overview

The hardware tiers system classifies any PC into one of five performance tiers based on its CPU architecture, instruction set support, memory bandwidth, and GPU capability. This classification drives automatic model selection, performance tuning, and tool availability decisions throughout the Limpet appliance.

### Design Principles

- **Deterministic.** The same hardware always produces the same tier. No heuristics that drift.
- **Conservative.** When in doubt, classify lower. An underestimated tier still works; an overestimated tier causes OOM kills.
- **Extensible.** New CPUs are added via TOML profiles, not code changes.
- **Offline-first.** The local hardware book ships with 200+ CPU profiles. Network lookup is a fallback.

---

## 2. Intel Tier Definitions

### Tier 1 — LIMITED (Pre-Haswell, no AVX2)
| Attribute | Value |
|-----------|-------|
| Generations | Sandy Bridge (2nd), Ivy Bridge (3rd), Haswell i3/Pentium |
| ISA | SSE4.2, AVX (no AVX2) |
| Memory | DDR3-1333/1600, dual-channel (~20-25 GB/s) |
| Typical systems | Dell OptiPlex 790/9020, HP EliteDesk 800 G1, Lenovo M93p |
| Impact | Scalar GGML fallback — ~50% slower than AVX2 path |

**Model limits:** 3B-7B quantized only (Q4_K_M). Larger models will OOM or crawl.

**Recommended models:**
- `qwen2.5-coder:7b-q4_K_M` — primary agent model (~3 tok/s)
- `qwen2.5:3b` — quick tasks, parsing (~8 tok/s)

**Performance tuning:**
- `OLLAMA_MAX_LOADED_MODELS=1` (RAM is tight)
- No P-core pinning (no hybrid architecture)
- `vm.swappiness=10`

**Notes:** These machines are approaching end-of-useful-life for LLM work but are common in e-waste streams. Limpet still functions — the MCP meta-server, all 104 tools **[U]**, and headless mode work fine. Agent mode is slow but functional.

### Tier 2 — CAPABLE (Haswell through 7th gen, AVX2, DDR3/DDR4)

| Attribute | Value |
|-----------|-------|
| Generations | Haswell (4th), Broadwell (5th), Skylake (6th), Kaby Lake (7th) |
| ISA | AVX2, FMA3 |
| Memory | DDR3-1600/DDR4-2133/2400, dual-channel (~25-38 GB/s) |
| Typical systems | Dell OptiPlex 7040/7050, HP EliteDesk 800 G3, Lenovo M910q, Mac Pro 2013 (E5 v2 Ivy Bridge-EP — exception: has AVX2 via Xeon) |
| Impact | Full AVX2 GGML path. Memory bandwidth is the bottleneck. |
**Model limits:** 7B-14B quantized. 32B possible on 64GB+ systems but slow.

**Recommended models:**
- `qwen2.5-coder:14b-q4_K_M` — primary agent model (~8-12 tok/s on DDR4)
- `qwen2.5-coder:7b` — fast agent, quick tasks (~15-20 tok/s)
- `qwen2.5:3b` — parsing, classification (~25+ tok/s)

**Performance tuning:**
- `OLLAMA_MAX_LOADED_MODELS=1` (2 on 64GB+ systems)
- `OLLAMA_FLASH_ATTENTION=1`
- No P-core pinning (no hybrid architecture)

**Notes:** The sweet spot for e-waste builds. DDR4 Skylake/Kaby Lake machines (6th-7th gen) are excellent value. DDR3 Haswell machines work but are noticeably slower on memory-bound inference.

### Tier 3 — RECOMMENDED (8th gen+, AVX2, DDR4-2666+)

| Attribute | Value |
|-----------|-------|
| Generations | Coffee Lake (8th-9th), Comet Lake (10th), Rocket Lake (11th) |
| ISA | AVX2, FMA3 |
| Memory | DDR4-2666/3200, dual-channel (~40-50 GB/s) |
| Typical systems | Dell OptiPlex 7070/7080, HP EliteDesk 800 G5/G6, Lenovo M80q, ThinkStation P340 |
| Impact | Solid all-around performance. The recommended minimum. |

**Model limits:** 14B is the sweet spot. 32B viable on 64GB+.

**Recommended models:**
- `qwen2.5-coder:14b` — primary agent model (~15-18 tok/s)
- `qwen2.5-coder:32b-q4_K_M` — deep analysis on 64GB+ (~4-6 tok/s)
- `qwen2.5:7b` — fast tasks (~25+ tok/s)
**Performance tuning:**
- `OLLAMA_MAX_LOADED_MODELS=2`
- `OLLAMA_FLASH_ATTENTION=1`
- `OLLAMA_NUM_PARALLEL=1`

### Tier 4 — OPTIMAL (12th gen+, hybrid P+E cores)

| Attribute | Value |
|-----------|-------|
| Generations | Alder Lake (12th), Raptor Lake (13th), Meteor Lake (14th+) |
| ISA | AVX2, AVX-VNNI (some have AVX-512 on P-cores only) |
| Memory | DDR4-3200/DDR5-4800+, dual-channel (~45-75 GB/s) |
| Typical systems | Lenovo M80s Gen 3, Dell OptiPlex 7010, HP EliteDesk 800 G9, any 12th gen+ desktop |
| Impact | Hybrid architecture enables P-core pinning for inference + E-cores for background work. |

**Model limits:** 14-32B primary. MoE models (35B with 3B active) excel here.

**Recommended models:**
- `qwen2.5-coder:32b` — deep analysis (~4-6 tok/s, P-cores)
- `qwen3.6:35b-a3b` — fast MoE agent (~15-25 tok/s, only 3B active params)
- `qwen2.5-coder:14b` — balanced (~18 tok/s)
- `qwen2.5:3b` — quick parsing (~40+ tok/s)

**Performance tuning:**
- Pin Ollama to P-cores: `taskset -c 0-15` (for 8P-core systems)
- E-cores (16-19) reserved for: Docker containers, build processes, tool execution
- `OLLAMA_MAX_LOADED_MODELS=2`
- `OLLAMA_FLASH_ATTENTION=1`
- `OLLAMA_NUM_PARALLEL=1`

**P-core detection algorithm (Intel):**
```python
import subprocess, re
# Parse lscpu for core types (12th gen+)
output = subprocess.check_output(['lscpu', '--json'], text=True)
# Look for "Thread(s) per core" and "Core(s) per socket"
# On hybrid CPUs, also check /sys/devices/system/cpu/cpu*/topology/core_type
# P-cores: core_type == "Core" or "performance"
# E-cores: core_type == "Atom" or "efficiency"
```

AMD has no P/E hybrid split on any shipping part as of this writing — the AMD-side
equivalent of this algorithm detects CCD/NUMA topology instead, not core type. See
§3.9; do not apply this P/E snippet to an AMD vendor string.

### Tier 5 — ENTHUSIAST (discrete GPU)

| Attribute | Value |
|-----------|-------|
| CPU | Any Tier 2-4 CPU with discrete GPU |
| GPU | NVIDIA (CUDA) or AMD (ROCm) with 6GB+ VRAM |
| Memory | System RAM + GPU VRAM |
| Typical systems | Any desktop/workstation with gaming/workstation GPU |
| Impact | GPU offload changes the inference equation entirely. |

**Model limits:** VRAM-dependent:
- 8GB VRAM: 7-14B fully offloaded
- 12GB VRAM: 14B fully, 32B partially
- 24GB VRAM: 32B fully offloaded
- 48GB+ VRAM: 70B+ models

**Recommended models:** Same as CPU tier, but with GPU layer offload:
- Ollama auto-detects GPU and offloads layers based on VRAM
- `OLLAMA_GPU_LAYERS=auto` (let Ollama decide)
- For partial offload: specify layers manually based on VRAM

**GPU detection:**
```bash
# NVIDIA
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
# AMD ROCm
rocm-smi --showmeminfo vram
# Intel Arc (future)
intel_gpu_top (limited support)
```

**Driver installation (during install wizard Step 9):**
- NVIDIA: `nvidia-driver-560` from Ubuntu repos (or latest from PPA)
- AMD: ROCm from AMD's official repo (`amdgpu-install`)
- Neither installed if no dGPU detected

---

## 3. AMD Tier Definitions (wq#3919 research pass, 2026-10-14; §3.9-§3.10 wq#3939)

**Evidence labels** used throughout this section. Nothing unlabeled should be trusted as a number.

| Label | Meaning |
|-------|---------|
| **[M]** | Measured by a third party; source cited in 3.7. Hardware, quant and build as the source states them. |
| **[F]** | Measured on our own fleet (lumen_state facts). |
| **[D]** | Derived by arithmetic from a vendor spec (e.g. channels x 8 bytes x MT/s). A theoretical ceiling, not an expectation. |
| **[V]** | Vendor/retailer spec listing. |
| **[U]** | Unvalidated: no measurement or primary source was found. Do not ship as a promise. Needs a bench run on real hardware. |

**What the data says, in one paragraph.** Token generation (tg) on every AMD part found is memory-bandwidth-bound, not compute-bound. Same-memory Zen 2 and Zen 3 parts generate tokens at the same rate (3900X 8.45 t/s vs 5950X 8.50 t/s on Mistral-7B Q6_K, both DDR4-3600) [M]. Moving DDR4-2666 to 3200 to 3600 on one 3900X moved tg 6.32 to 7.55 to 8.45 (+19%, +34%) [M]. Prompt processing (pp) is compute-bound and is where newer cores and AVX-512 pay off (Zen 3 +58% over Zen 2 at DDR4-3600, 109 vs 69 t/s pp512) [M]. Tiers are therefore assigned memory bandwidth first, ISA second, core count last.

### 3.0 Relationship to the earlier wq#3835 research doc

`research/WQ3835_AMD_CPU_TIER_RESEARCH.md` (PR #1830, merged) already exists and this section was written without it. It is a single-seat answer from DeepSeek (the Gemini and Cerebras council seats were down) plus fleet-measured BC-250 data; its own text says every non-BC-250 tok/s figure is reasoning, not measurement. Reconciliation:

- **Agrees with this section:** gate on AVX2 first; bucket by memory generation and channel count rather than Zen generation; default every RDNA2/RDNA3 APU to Vulkan and do not offer a ROCm toggle; place BC-250 by its Vulkan GPU path, not its Zen 2 CPU; channel count beats core count on Threadripper/EPYC. It also had BC-250 right where the earlier spec draft had it wrong.
- **Superseded by measurement:** its 7B Q4 tok/s expectations. Zen 2 "12-17" is roughly in line at the low end (3900X DDR4-3200 measured 7.55 on Q6_K, ~11 if scaled to Q4_0 by a 1.46x bytes-per-weight ratio **[D]**). Zen 3 "16-22" runs about 1.3-2x above the nearest measured points (5950X DDR4-3600 8.50 on Q6_K, ~12.4 Q4_0-equivalent **[D]**; 5600X DDR4-3000 Q4_0 8.95 **[M]**). Zen 4 "25-35" is about 2-3x above the only measured Zen 4 point (7940HS 7B Q4_K_M tg 11.19 **[M]**). Do not use those ranges as installer promises; use the measured anchors in 3.1 and 3.4, and bench per 3.8.
- **Different tier numbering:** that doc uses T0-T5; this spec keeps the Intel-aligned Tier 1-5 numbering. It would drop FX entirely (unsupported) whereas 3.1 keeps FX at Tier 1. That is a policy call for Frank, not settled here.
- Its Zen 1 claim (no real penalty because decode is bandwidth-bound) is plausible and consistent with the bandwidth rule above, but it is the same reasoning-only evidence: still **[U]** until benched.

### 3.1 Tier mapping (updated)

| AMD generation | ISA | Tier | Basis |
|---------------|-----|------|-------|
| FX (Bulldozer/Piledriver) | AVX1 only, no AVX2 | Tier 1 | Prior spec; no AVX2 path. [U] no AMD FX llama.cpp numbers found. |
| Zen 1 (Ryzen 1000, 2000 "Zen+") | AVX2 (prior spec: executed as 128-bit halves) | Tier 2 | **[U]** no llama.cpp measurement found for any Zen 1/Zen+ part. The half-width AVX2 claim is carried from the prior draft and was not re-verified. Memory tops out at DDR4-2666 on Zen 1, so tg should track the bandwidth rule above, pp is the likely penalty. Run the 3.8 bench plan on a real 1600/1700 before publishing any Zen 1 number. |
| Zen 2 (Ryzen 3000) | Full AVX2 | Tier 3 | [M] 3900X: Mistral-7B Q6_K pp512 68.65 / tg 6.32 at DDR4-2666, tg 8.45 at DDR4-3600. |
| Zen 3 (Ryzen 5000) | Full AVX2 | Tier 3 | [M] 5950X DDR4-3600: Mistral-7B Q6_K pp512 109.37 / tg 8.50. 5600X DDR4-3000: Mistral-7B Q4_0 pp512 23.70 / tg16 8.95 (older llamafile build, pp low; do not compare to the 5950X pp). |
| Zen 4 (Ryzen 7000) | AVX-512 (256-bit double-pumped) | Tier 4 | [M] 7950X llama.cpp 8B Q4_0 pp512 153.52 (16 thr); mobile 7940HS Mistral-7B Q4_K_M pp512 82.77 / tg16 11.19. Dual-channel DDR5 caps tg. |
| Zen 5 (Ryzen 9000) | AVX-512 (256- or 512-bit per part) | Tier 4 | Mobile Ryzen AI 7 350 CPU-only, llama 7B Q4_0: pp512 31.4-36.2 / tg128 10.1-10.5 [M]. No desktop Ryzen 9000 llama.cpp numbers were located. [U] for desktop. |

Note: older drafts mapped Zen 4/5 to "Tier 4 ~ 12th gen+". Keep the tier, but do not promise Intel-equivalent tok/s: tg is set by DDR5 dual-channel bandwidth on every desktop Zen 4/5.

### 3.2 Findings by research question

**(1) Zen 1 half-width AVX2.** No third-party llama.cpp results for Zen 1 or Zen+ were found across llama.cpp discussions, llamafile discussion #450, ik_llama.cpp discussion #164, or general search. **[U]** Penalty size is unknown. Config workaround candidates to test: Q4_0/Q4_K_M quants (cheapest per byte), `--threads` = physical cores. Do not quote a tok/s figure for Zen 1 in marketing or the wizard.

**(2) Zen 2/3 full AVX2, CCD topology, thread count.**
- tg is bandwidth-bound: see 3900X memory-speed scaling above [M]. Installer advice: tell the user that XMP/DOCP at the CPU's rated 3200 or 3600 is worth ~19-34% tg over JEDEC 2666 on Zen 2.
- Zen 3 vs Zen 2 at equal memory: tg equal (8.50 vs 8.45), pp +58% [M].
- Thread count: on Ryzen AI 7 350 (Zen 5 mobile) llama 7B Q4_0, tg128 was 10.49 / 10.27 / 10.06 at 4 / 8 / 16 threads, pp512 31.37 / 35.84 / 36.16 [M]. Going beyond 4 threads gave +14% pp then +1% pp and slightly lower tg. Community guidance in llama.cpp discussion #3167 is that SMT usually does not help ("presumably completely memory bound"), though one Xeon user reports the opposite. Default: `--threads` = physical cores, then let the install wizard run the 3.8 sweep and store the winner.
- CCD / Infinity Fabric: **the old "~10-15% for dual-CCD" figure in the prior draft was an estimate and is removed.** No consumer dual-CCD measurement was found. The only direct evidence of placement sensitivity is EPYC 7H12 under llamafile: pp512 ranged from 29.45 to 230.92 t/s and tg16 from 5.14 to 9.21 across default / `-t 128` / `--numa distribute` / `numactl` / core-binding runs on one machine [M, single machine, noisy]. Treat thread pinning as mandatory to test on multi-CCD/NUMA parts, optional on single-CCD parts.

**(3) Zen 4/5 AVX-512 in llama.cpp / Ollama.**
- Zen 4 implements AVX-512 as 256-bit double-pumped; Zen 5 can use a 256-bit or native 512-bit path depending on the part, and EPYC 9005 is documented at 512-bit with a BIOS switch to 256-bit or off [Phoronix, 3.7].
- llama.cpp/ggml detects CPU features at runtime and selects the most capable implementation [ggml CPU backend docs]. Phoronix llama.cpp CPU captions on a Zen 5 part (Strix Halo) read "AVX-512 On was the fastest" for pp on 8B Q8_0 and 7B Q8_0 models, with no figures in text. llamafile 0.7 release coverage cites ~10x faster prompt evaluation on Zen 4 from unnamed testers (anecdotal, not a benchmark).
- Measured Zen 4 (7940HS, llamafile): Mistral-7B pp512 BF16 95.03 > Q6_K 85.63 > Q4_K_M 82.77 > Q8_0 53.19, tg16 Q4_K_M 11.19. BF16 prompt processing beating quantized on this part is consistent with AVX-512 BF16 being used. Source lists the memory as "DDR4@5600"; treat as a typo, type unverified.
- Software matters as much as ISA: on a 7950X, ik_llama.cpp got pp512 273.54 vs llama.cpp 153.52 on 8B Q4_0 (16 thr), and 256.90 vs 78.58 on BF16 [M].
- AVX-512 does not raise tg meaningfully because tg is bandwidth-bound (see summary). Do not claim a tg uplift from AVX-512.
- **[U] Ollama:** whether Ollama's prebuilt CPU runner selects an AVX-512 variant on Zen 4/5 was not confirmed from a primary source. Wizard check: after first model load on a Zen 4/5 box, parse the Ollama startup `system info` line and confirm `AVX512 = 1`; if 0, offer a llama.cpp source build with `-DGGML_AVX512=ON -DGGML_AVX512_VNNI=ON -DGGML_AVX512_BF16=ON`.

**(4) Threadripper (quad-channel).**
- 7980X [V, Scan listing]: 64C/128T, 3.2 / 5.1 GHz, 256 MB L3, 350 W, DDR5-5200, quad-channel, 1024 GB max, ECC. Derived peak bandwidth 4 x 8 B x 5200 = **166.4 GB/s [D]** (older quad-channel DDR4-3200 Threadripper 3000: 102.4 GB/s [D]). The prior draft's "80-100 GB/s" was a DDR4-era estimate.
- Measured: Threadripper PRO 7995WX (Zen 4), Mixtral-8x22B Q6_K, llamafile: pp512 151.73 / tg 8.07, versus Ryzen 9 5950X on the same model and quant: pp512 19.66 / tg 1.57 [M]. That is 7.7x pp and 5.1x tg. Memory configuration of the 7995WX run was not stated (PRO parts have more channels than the 7980X, **[U]** how much of the gap transfers to a 7980X).
- Threadripper PRO 3995 reports a single NUMA node and 128 threads in llama.cpp #3167 [M, anecdote]; 3000-series is "effectively configured for node interleave". Check `lscpu | grep NUMA` before choosing a NUMA mode.

**(5) EPYC (8 to 12 channel).**
- EPYC 9554 [V, TechPowerUp]: 64C/128T, 3.1 / 3.75 GHz, 256 MB L3, 360 W (cTDP 320-400), 12-channel DDR5-4800, ECC, AVX-512, VNNI, eight compute dies. Derived peak bandwidth 12 x 8 B x 4800 = **460.8 GB/s [D]**.
- Channel population dominates: EPYC 9654 (Zen 4, 96C) running a 34B q4_0 model measured 1.5 t/s with one 64 GB DIMM and 4 t/s with two 32 GB DIMMs [M, llama.cpp #3167 comment, Mar 2024]. The installer must warn when populated memory channels are fewer than the platform's channels.
- EPYC 7502P (Zen 2): Llama-2 13B Q4_0, 32 threads, pp 19.38 / tg 7.85 [M, 2023 build].
- Is it overkill, and does it unlock interactive 70B? **[U]** no 70B measurement on a 12-channel EPYC was found. Ceiling for reasoning only: 70B Q4_K_M is roughly 40 GB of weights (approximate), so 460.8 GB/s / 40 GB gives about 11.5 t/s as a hard upper bound [D]. Real EPYC runs above sit far below their bandwidth ceilings, so expect meaningfully less until measured. Fit-in-RAM is the real unlock: 256 GB to 1.5 TB lets several large models stay resident.
- Multi-socket: not measured; default to single-socket recommendations and pin to one NUMA node unless a bench says otherwise.

**(6) APU ROCm: Ryzen 8700G / 780M and RDNA2 vs RDNA3 iGPU.**
- **Support status [U]:** AMD's Ryzen ROCm compatibility table could not be retrieved this session (the fetched page was the section landing page), so official support for 780M (RDNA3 iGPU) is unconfirmed. Do not claim "ROCm supported on 8700G".
- ROCm result found: one community report on a Ryzen Pro 7 8700GE, ROCm 7.1 (Arch AUR) with llama.cpp b7728, "Llama-3.2-8B-Instruct-Q4_K_M" (file name as posted), `--gpu-layers 2`, `HIP_VISIBLE_DEVICES=0`, `GGML_CUDA_ENABLE_UNIFIED_MEMORY=1`: pp 29.7 / tg 10.7 t/s. Single run, prompt "Hello again", no CPU or Vulkan baseline. The post reports target gfx1100; `HSA_OVERRIDE_GFX_VERSION=11.0.0` is mentioned only as a fallback if ROCm misdetects the iGPU. Treat as **anecdote [U]**, not a benchmark.
- Vulkan result found (llama.cpp Vulkan discussion #10879, Llama 2 7B Q4_0): the page lists APU series without naming chips: Ryzen 7000 series pp512 281.62 / tg128 19.91 (flash-attn: 312.85 / 20.09), Ryzen 8000 series pp512 267.27 / tg128 19.81 (FA: 311.02 / 21.28), Ryzen 6000 series (RDNA2-class iGPU) pp512 240.89 / tg128 21.26 (FA: 277.91 / 21.15) [M, chips unnamed]. The page names series, not chips or GPU generations (Ryzen 7000 alone spans RDNA2 and RDNA3 iGPUs), so RDNA2-vs-RDNA3 cannot be separated from this data **[U]**. All three series land at tg128 ~20-21 t/s, consistent with a shared-system-RAM bandwidth limit.
- Phoronix Lemonade review (text only, Strix Halo and a discrete Radeon, not 780M): Vulkan delivered more tokens per second, ROCm had lower latency / better time-to-first-token in some cases [M].
- **Recommendation for 780M-class iGPUs:** default to the Vulkan backend (our own fleet already runs llama.cpp Vulkan on AMD GPUs, see (7)). Offer ROCm only as an experimental opt-in with a bench gate. The iGPU shares system RAM, so the DDR5 bandwidth rule still applies and the win is mostly pp, not tg.

**(7) Consumer Radeon / older AMD GPUs.**
- Fleet-validated [F]: Nephthys carries 2x FirePro D500 (Tahiti LE, GCN 1.0). ROCm/CUDA are not applicable; Mesa RADV Vulkan works. llama.cpp b11228 Vulkan, Qwen3-1.7B Q4_K_M, no flash-attn: pp512 321-343 t/s vs 99 CPU-only, tg128 47 vs 31 (wq#2501). Lesson for Limpet: Vulkan is the safe path for old Radeon cards that ROCm dropped. RADV needs root or the `render` group.
- **BC-250 correction [F]:** the prior draft said "BC-250 (mining card, Navi 12): ROCm works, 8GB HBM2". That is wrong for our fleet. BC-250 is the Cyan Skillfish APU, Zen 2 6C/12T with **gfx1013** (GFX10.1, "RDNA1.5"), **16 GB GDDR6 unified** shared CPU+GPU (stock BIOS splits 8 GB RAM / 8 GB VRAM; dynamic split needs a patched BIOS plus ttm tuning). ROCm userspace ships no gfx1013 support and the `HSA_OVERRIDE_GFX_VERSION=10.3.0` (gfx1030) spoof is explicitly not advisable. **Vulkan is the only usable GPU compute path.** Strength is small/MoE models (community-reported ~78.7 tok/s on a ~3.6B-active MoE at the 40-CU unlock). Not a large-dense-model host. Thermals: heatsink-limited under sustained load (GPU edge 89.6C avg / 107C peak in a 10 minute llama-bench, ~10% throughput drop from throttle).
- `HSA_OVERRIDE_GFX_VERSION` per-card table: **[U]** the source article could not be fetched. Keep the prior draft's guidance (RX 6600-6800 may need it; RX 7900 XTX is a first-class ROCm card) labeled unverified until a primary source is read, and never recommend a spoof that crosses ISA generations (see BC-250 above).

**Consumer Radeon ROCm support matrix (summary for the wizard):**

| Card / family | ROCm status | Installer default | Notes |
|---|---|---|---|
| RX 7900 XTX/XT (RDNA3, gfx1100) | Officially supported | ROCm | First-class target in AMD's rocBLAS fat binaries [V]. |
| RX 7600-7800 (RDNA3, gfx1101/gfx1102) | Mixed/unsupported in official fat binaries | Vulkan, ROCm opt-in | Same family gap as the APU gfx1102/gfx1103 problem in (6); confirm with `rocminfo` before offering ROCm. |
| RX 6600-6800 (RDNA2, gfx1030-gfx1032) | Supported on gfx1030; narrower parts need `HSA_OVERRIDE_GFX_VERSION=10.3.0` per community reports | Vulkan default, ROCm opt-in with override | Override table itself is **[U]** (3.2.7); do not auto-apply, surface as an advanced toggle (§3.10). |
| RX 5000 / Vega / Polaris (gfx900s/gfx1010) | Dropped from current ROCm | Vulkan only | Mirrors the FirePro D500 (older GCN) fleet finding [F]. |
| Integrated RDNA2/RDNA3 (780M, 760M, 680M) | Not in default rocBLAS fat binaries | Vulkan only | See (6); do not expose a ROCm toggle by default. |
| BC-250 (gfx1013) | No ROCm userspace target exists | Vulkan only, no toggle | ISA-mismatch override is actively unsafe (3.2.7); hard-disable ROCm for this device ID, not just default-off. |

**(8) Power-user settings (Threadripper / EPYC vs consumer).** Guidance from the llama.cpp CPU tuning guide at bigiron.cc (guidance, not measured) plus our own evidence above:
- `--threads` = physical cores (example: 16 on a 16C/32T 7950X); add `--cpu-strict 1` to stop thread migration.
- Multi-NUMA / dual-CCD: start with `--numa distribute`, benchmark against `numactl --cpunodebind=0 --membind=0`. Our only measured evidence (EPYC 7H12) shows a ~8x swing in pp across binding modes.
- `--mlock --no-mmap` for servers; requires `LimitMEMLOCK=infinity` in the systemd unit. Defaults `--batch-size 2048`, `--ubatch-size 512` are fine for bandwidth-bound CPUs.
- Populate every memory channel (EPYC 9654 1.5 -> 4 t/s from DIMM population alone).
- Do not stack CPU-saturating jobs on thermally or power-marginal hosts (fleet lesson from the Mac Pro power-loss incidents).
- Numbers from insiderllm.com ("Ryzen 5 5600X 5-7 tok/s, 7950X 14-18 tok/s on Llama 3.1 8B Q4_K_M") were **rejected**: they are unsourced ranges and the 5600X figure is below measured 5600X/5950X results (8.95 and 8.50 t/s on 7B models).

### 3.3 Special AMD tiers (updated)

**Tier 4A, Threadripper (quad-channel; 64-1024 GB).** 7980X: DDR5-5200 quad-channel, 166.4 GB/s theoretical [D][V]. One measured sibling (PRO 7995WX) ran Mixtral-8x22B Q6_K at tg 8.07, 5.1x a 5950X on the same model [M]. Whether `qwen2.5-coder:72b-q4_K_M` is interactive on a 7980X is **[U]**; do not promise it until benched.

**Tier 4E, EPYC (256 GB-1.5 TB; 8-12 channel).** EPYC 9554: 12-channel DDR5-4800, 460.8 GB/s theoretical [D][V]. Gate the tier on populated channel count, not on CPU model: a 9654 with one DIMM measured 1.5 t/s vs 4 t/s with two [M]. `OLLAMA_MAX_LOADED_MODELS=4+` is reasonable only when RAM holds them resident. Note on channel count: older EPYC (Naples/Rome/Milan) ships 8-channel DDR4; Genoa/Bergamo/Turin (Zen 4/5) moved to 12-channel DDR5 — size the bandwidth formula (§3.1's "channels x 8 B x MT/s") from the actual SKU's channel count, not a fixed "8-channel" assumption, since 8 vs 12 channels is a ~1.5x bandwidth difference at the same speed grade.

**Tier 5A, AMD GPU.**
- Radeon discrete with current ROCm support: best path, ROCm or Vulkan; bench both [U for specific cards here].
- Old GCN cards (Tahiti/Hawaii class): Vulkan via RADV, validated on our D500s [F].
- BC-250 (gfx1013, 16 GB GDDR6 unified): Vulkan only, small/MoE models only [F]. See correction above.
- Ryzen APU iGPU (780M class): Vulkan default, ROCm experimental [U].

### 3.4 TOML hardware-book profiles (AMD)

Spec fields come from vendor listings [V]; `tier` and `performance` are Limpet policy. Measured anchors are in comments. CCD counts are only stated where a source gave one. Format matches section 4. These five profiles are also shipped as standalone files under `limpet/hardware-book/amd/` (§3.4.1) so the install wizard's TOML loader can read them without extracting code fences from this doc.

```toml
# /etc/limpet/hardware-book/amd/ryzen-5-3600.toml
[cpu]
name = "AMD Ryzen 5 3600"
vendor = "amd"
family = "zen2"
codename = "Matisse"

[cpu.cores]
physical = 6
threads = 12
base_clock_mhz = 3600
boost_clock_mhz = 4200

[cpu.isa]
avx2 = true
avx512 = false

[cpu.memory]
type = "ddr4"
max_speed_mhz = 3200      # vendor max data rate
channels = 2
max_capacity_gb = 128

[tier]
classification = 3
reason = "Zen 2 full AVX2, DDR4-3200 dual channel; tg is bandwidth-bound"

[performance]
threads = 6               # physical cores; community reports say SMT usually does not help (mixed)
cpu_strict = true
mlock = true
# Anchor [M]: Zen 2 3900X, Mistral-7B Q6_K: tg 6.32 @DDR4-2666, 7.55 @3200, 8.45 @3600
# Wizard: recommend XMP/DOCP to the CPU's rated speed
```

```toml
# /etc/limpet/hardware-book/amd/ryzen-7-5800x.toml
[cpu]
name = "AMD Ryzen 7 5800X"
vendor = "amd"
family = "zen3"
codename = "Vermeer"

[cpu.cores]
physical = 8
threads = 16
base_clock_mhz = 3800
boost_clock_mhz = 4700

[cpu.isa]
avx2 = true
avx512 = false

[cpu.memory]
type = "ddr4"
max_speed_mhz = 3200
channels = 2
max_capacity_gb = 128

[tier]
classification = 3
reason = "Zen 3 full AVX2; ~+58% pp over Zen 2 at equal memory, equal tg"

[performance]
threads = 8
cpu_strict = true
mlock = true
# Anchor [M]: Zen 3 5950X DDR4-3600, Mistral-7B Q6_K: pp512 109.37, tg 8.50
```

```toml
# /etc/limpet/hardware-book/amd/ryzen-9-7950x.toml
[cpu]
name = "AMD Ryzen 9 7950X"
vendor = "amd"
family = "zen4"
codename = "Raphael"

[cpu.cores]
physical = 16
threads = 32
base_clock_mhz = 4500
boost_clock_mhz = 5700

[cpu.isa]
avx2 = true
avx512 = true             # 256-bit double-pumped on Zen 4
avx512_vnni = true        # TechPowerUp lists VNNI for Zen 4 EPYC; confirm on this SKU at detect time
# avx512_bf16: detect at runtime from /proc/cpuinfo (not verified from a primary source in wq#3919)

[cpu.memory]
type = "ddr5"
max_speed_mhz = 5200      # 2 DIMMs; AMD lists DDR5-3600 for 4 DIMMs
channels = 2
max_capacity_gb = 128

[cpu.topology]
ccd_count = 2             # inferred from AMD "package die count 3" (2 CCD + IOD); not stated directly

[tier]
classification = 4
reason = "Zen 4 AVX-512, DDR5 dual channel; tg capped by bandwidth"

[performance]
threads = 16
cpu_strict = true
mlock = true
numa_mode = "test"        # dual-CCD: run wizard sweep, compare vs numactl bind
avx512_check = "ollama system-info must show AVX512=1; else offer source build"
# Anchors [M]: llama.cpp 8B Q4_0 pp512 153.52 (ik_llama 273.54); mobile 7940HS Mistral-7B Q4_K_M tg16 11.19
```

```toml
# /etc/limpet/hardware-book/amd/threadripper-7980x.toml
[cpu]
name = "AMD Ryzen Threadripper 7980X"
vendor = "amd"
family = "zen4"
codename = "Storm Peak"

[cpu.cores]
physical = 64
threads = 128
base_clock_mhz = 3200
boost_clock_mhz = 5100

[cpu.memory]
type = "ddr5"
max_speed_mhz = 5200
channels = 4
max_capacity_gb = 1024
ecc = true

[tier]
classification = "4A"
reason = "Quad-channel DDR5-5200 = 166.4 GB/s theoretical [D]"

[performance]
threads = 64              # physical cores; sweep upward only if wizard bench wins
cpu_strict = true
numa_mode = "distribute"  # start here, bench vs numactl --cpunodebind=0 --membind=0
mlock = true
# Related [M]: PRO 7995WX Mixtral-8x22B Q6_K pp512 151.73 tg 8.07 vs 5950X 19.66 / 1.57
# UNVALIDATED: 72B interactive speed on this SKU
```

```toml
# /etc/limpet/hardware-book/amd/epyc-9554.toml
[cpu]
name = "AMD EPYC 9554"
vendor = "amd"
family = "zen4"
codename = "Genoa"

[cpu.cores]
physical = 64
threads = 128
base_clock_mhz = 3100
boost_clock_mhz = 3750

[cpu.isa]
avx2 = true
avx512 = true
avx512_vnni = true

[cpu.memory]
type = "ddr5"
max_speed_mhz = 4800
channels = 12
ecc = true

[cpu.topology]
ccd_count = 8             # TechPowerUp: 8x 72 mm2 compute dies

[tier]
classification = "4E"
reason = "12-channel DDR5-4800 = 460.8 GB/s theoretical [D]; gate on populated channels"

[performance]
threads = 64
cpu_strict = true
numa_mode = "distribute"
mlock = true
require_populated_channels = 12   # warn below this; EPYC 9654 measured 1.5 vs 4 t/s with 1 vs 2 DIMMs
max_loaded_models = 4
# UNVALIDATED: 70B interactive speed (hard ceiling ~11.5 t/s [D])
```

#### 3.4.1 Standalone hardware-book files

The five TOML blocks above are shipped verbatim as standalone files so the install
wizard's loader (§6.1) can read them without parsing markdown code fences:

```
limpet/hardware-book/amd/ryzen-5-3600.toml
limpet/hardware-book/amd/ryzen-7-5800x.toml
limpet/hardware-book/amd/ryzen-9-7950x.toml
limpet/hardware-book/amd/threadripper-7980x.toml
limpet/hardware-book/amd/epyc-9554.toml
```

Each parses standalone with `tomllib` (stdlib, no dependency — same discipline as
`limpet/manifest.py`, `design/wq3810_limpet_plugin_system.md:105-107`). The ISO build
copies this directory to `/etc/limpet/hardware-book/amd/` per §6.1; it is additive to
the existing `limpet/hardware.d/` USB/driver catalog (`limpet/hardware.d/build_iso_drivers.py`),
not a replacement — that catalog answers "what driver does this USB device need",
this one answers "what tier and tuning does this CPU get".

### 3.5 Hardware-book detection additions

- Read `lscpu` NUMA node count and memory channel population (`dmidecode -t memory`); warn when populated channels < platform channels.
- Record `AVX512*` flags from `/proc/cpuinfo`; verify the inference runtime actually reports them after first model load.
- Run the 3.8 sweep once at install and store the winning `threads` / `numa_mode` per machine.
- Run the CCD/NUMA topology probe in §3.9 on every AMD vendor string before applying a `numa_mode` default, since the dual-CCD/single-CCD split (not core count) is what decides whether NUMA pinning is worth testing at all.

### 3.6 Open items (do not publish as fact until closed)

1. Zen 1 / Zen+ llama.cpp penalty: **[U]**.
2. Desktop Zen 5 (Ryzen 9000) numbers: **[U]**.
3. Whether Ollama's prebuilt CPU runner uses AVX-512 on Zen 4/5: **[U]**.
4. Official ROCm support and the correct gfx target for 780M / 8700G: **[U]**. Verify with `rocminfo` on real hardware.
5. 70B on 12-channel EPYC and 72B on a 7980X: **[U]**.
6. Consumer dual-CCD placement penalty: **[U]** (prior "10-15%" removed).
7. The CCD-grouping heuristic in §3.9 (shared-L3 fallback) has not been validated against a real dual-CCD box in this fleet — **[U]**, flag the same way as the other open items until a Zen 4+ multi-CCD machine is available to check `lscpu -e` output against it.

### 3.7 Sources

- llamafile "Lots of CPU benchmarks" discussion #450 (Zen 2/3/4 rows; EPYC 7H12 binding runs; 7995WX): https://github.com/mozilla-ai/llamafile/discussions/450
- ik_llama.cpp "Latest CPU performance comparison" #164 (Ryzen 7950X): https://github.com/ikawrakow/ik_llama.cpp/discussions/164
- llama.cpp "CPU Performance" #3167 (EPYC 7502P, EPYC 9654 DIMM population, SMT comments): https://github.com/ggml-org/llama.cpp/discussions/3167
- llama.cpp "Performance of llama.cpp with Vulkan" #10879 (Ryzen 6000/7000/8000 series Vulkan rows): https://github.com/ggml-org/llama.cpp/discussions/10879
- llama.cpp discussion #19235 (8700GE, ROCm 7.1): https://github.com/ggml-org/llama.cpp/discussions/19235
- Ryzen AI 7 350 CPU-only llama-bench (thread scaling): https://www.mgroeber.de/benchmarking-llamacpp-on-legacy-hardware-2.html
- Phoronix, AVX-512 on EPYC 9005 / Zen 5 data path: https://www.phoronix.com/review/amd-epyc-9755-avx512 ; Zen 4 AVX-512 and llamafile 0.7: https://www.phoronix.com/news/Llamafile-0.7 ; Strix Halo AVX-512: https://www.phoronix.com/review/amd-strix-halo-avx512/4 ; Lemonade ROCm vs Vulkan: https://www.phoronix.com/review/lemonade-vulkan-rocm/3
- ggml CPU backend runtime feature selection (third-party docs mirror): https://mintlify.wiki/ggml-org/ggml/backends/cpu
- CPU tuning guidance: https://www.bigiron.cc/guides/llama-cpp-tuning-for-cpu-only-homelab-inference
- Specs: EPYC 9554 https://techpowerup.com/cpu-specs/epyc-9554.c2930 ; Threadripper 7980X https://www.scan.co.uk/products/amd-ryzen-threadripper-7980x-tr5-zen-4-64-core-128-thread-32ghz-51ghz-320mb-cache-pcie-50-350w-cpu ; Ryzen 9 7950X https://www.amd.com/en/products/processors/desktops/ryzen/7000-series/amd-ryzen-9-7950x.html ; Ryzen 5 3600 vs 7 5800X https://www.eatyourbytes.com/cpu-comparison/amd-ryzen-5-3600_amd-ryzen-7-5800x/
- Fleet facts (lumen_state): nephthys.gpu (wq#2501 Vulkan bench), bc250.memory_ceiling, bc250.training_and_autoevolve_assessment.

### 3.8 Bench plan to close the [U] items

```bash
# per target machine, same model for every run (Qwen3-8B Q4_K_M or Llama-3.1-8B Q4_K_M)
llama-bench -m MODEL.gguf -t 4,6,8,12,16 -p 512 -n 128 -r 3
# NUMA / pinning (multi-CCD, TR, EPYC)
llama-bench -m MODEL.gguf -t 16 --numa distribute -p 512 -n 128 -r 3
numactl --cpunodebind=0 --membind=0 llama-bench -m MODEL.gguf -t 16 -p 512 -n 128 -r 3
# iGPU
llama-bench -m MODEL.gguf -ngl 99 -p 512 -n 128 -r 3     # Vulkan build, then ROCm build
```

### 3.9 AMD topology detection (no P/E split — CCD/NUMA instead)

Intel's Tier 4 detection (§2) branches on P-core/E-core `core_type`. No shipping AMD
part exposes that split — every core on a Ryzen/Threadripper/EPYC die is identical.
The AMD-equivalent signal the installer needs is **which cores share a CCD** (compute
die), because that is what decides whether `--numa distribute` / `numactl` pinning is
worth testing (§3.2.2, §3.2.8): single-CCD desktop parts (Ryzen 5/7/9 non-X3D) see
no measured placement sensitivity, while multi-CCD/multi-socket parts (Threadripper,
EPYC) show up to an 8x pp swing across binding modes on the one machine measured
(EPYC 7H12, §3.2.2).

```python
import subprocess, os, re
from collections import defaultdict

def detect_amd_topology():
    """Group logical CPUs by CCD for an AMD part. No P/E split exists on AMD;
    this is the CCD-topology analogue of the Intel P-core detector in §2."""
    cpu_dirs = sorted(
        d for d in os.listdir('/sys/devices/system/cpu')
        if re.fullmatch(r'cpu\d+', d)
    )

    # Primary signal: per-core die_id (kernel reports this directly on multi-die
    # packages since 5.9; each CCD is a separate die). Falls back to grouping by
    # shared L3 cache topology when die_id is absent or collapses to one value —
    # each CCD on Zen has its own L3 slice, so cores sharing index3 share a CCD.
    die_groups = defaultdict(list)
    l3_groups = defaultdict(list)
    for d in cpu_dirs:
        cpu_num = int(d[3:])
        try:
            with open(f'/sys/devices/system/cpu/{d}/topology/die_id') as f:
                die_groups[f.read().strip()].append(cpu_num)
        except OSError:
            pass
        try:
            with open(f'/sys/devices/system/cpu/{d}/cache/index3/shared_cpu_list') as f:
                l3_groups[f.read().strip()].append(cpu_num)
        except OSError:
            pass

    if len(die_groups) > 1:
        ccd_groups = list(die_groups.values())
    elif l3_groups:
        ccd_groups = list(l3_groups.values())
    else:
        ccd_groups = [list(range(len(cpu_dirs)))]  # single CCD, nothing to split

    numa_nodes = subprocess.run(
        ['lscpu', '-p=NODE'], capture_output=True, text=True, check=False
    ).stdout
    numa_node_count = len({
        line for line in numa_nodes.splitlines() if line and not line.startswith('#')
    }) or 1

    return {
        'ccd_count': len(ccd_groups),
        'ccd_groups': ccd_groups,   # list of logical-CPU lists, one per CCD
        'numa_node_count': numa_node_count,
        'needs_pinning_bench': len(ccd_groups) > 1 or numa_node_count > 1,
    }
```

**How this feeds tiering and tuning:** `ccd_count`/`numa_node_count` never change the
tier number (§3.1 tiers are set by ISA + memory generation + channel count, not core
topology) — they set `needs_pinning_bench`, which gates whether the install wizard
runs the §3.8 NUMA sweep at all. A single-CCD Ryzen 7 5800X (`ccd_count=1`) skips the
sweep and uses the TOML defaults in §3.4; a Threadripper or EPYC (`ccd_count>1`) runs
it and overwrites `[performance].numa_mode` with whichever of `distribute` /
`numactl --cpunodebind` measured faster on that specific box, per §3.4's
`numa_mode = "test"` / `"distribute"` placeholders. The L3-grouping fallback is a
heuristic, not a verified AMD topology API — logged as open item 7 in §3.6.

### 3.10 Advanced settings exposure for power users

Every knob named in §3.2.8 and baked into the §3.4 TOML `[performance]` tables is a
**read-only installer default**, not a hidden ceiling — Threadripper/EPYC operators
need to override them when the §3.8 bench picks a different winner than the shipped
default, and that override path has to exist without editing a TOML file by hand on
a headless box. This reuses the settings-panel layering `docs/settings-spec.md:150-168`
already specifies for the hardware-tier domain (§4.2 there) rather than inventing a
second config surface:

| Knob | `limpet-config` key | Default source | Who needs to touch it |
|---|---|---|---|
| Thread count | `tuning.cpu_threads` | TOML `[performance].threads` (physical core count) | Any AMD tier; rarely overridden below Tier 4A |
| NUMA mode | `tuning.numa_mode` | §3.9's `needs_pinning_bench` result, else TOML default | Threadripper (4A), EPYC (4E) — single-CCD desktop parts never need this |
| Strict CPU affinity | `tuning.cpu_strict` | `true` for every AMD profile in §3.4 | Multi-tenant EPYC boxes running other workloads alongside Ollama |
| Required populated channels | `tuning.require_populated_channels` | TOML `[performance].require_populated_channels` (EPYC only) | EPYC (4E) operators adding/removing DIMMs |
| Max resident models | `tuning.resident_model_max_params` / `max_loaded_models` | RAM-aware matrix (§7) × tier | EPYC (4E) operators with 256 GB+ who want more than one large model resident |
| AVX-512 runtime check | `tuning.avx512_check` (read-only status, no override) | Ollama `system info` parse (§3.2.3) | Zen 4/5 (Tier 4) operators chasing a missing AVX-512 path |
| ROCm opt-in override | `tuning.rocm_experimental` | `false` everywhere in §3.3's Tier 5A / §6 ROCm matrix | APU/consumer-GPU operators explicitly accepting the [U]-labeled ROCm risk in §3.2.6-7 |

Same bounds discipline as `docs/settings-spec.md:124-127`: each knob's valid range is
derived from the hardware row (§3.4's TOML, or §3.9's live topology probe), not
hand-typed, so `limpet-config set tuning.cpu_threads 256` on a 64-core EPYC fails
closed (exit 2) instead of silently oversubscribing. `rocm_experimental` is the one
boolean override that exists purely to let a power user accept a documented [U] risk
— setting it to `true` does not change any tier or TOML default, it only unlocks the
ROCm backend choice in the GPU-detection step (§2 Tier 5) for a device this spec
otherwise defaults to Vulkan.

---

## 4. Hardware Book Format
CPU profiles are stored as TOML files in `/etc/limpet/hardware-book/`:

```toml
# /etc/limpet/hardware-book/intel/i7-12700.toml

[cpu]
name = "Intel Core i7-12700"
vendor = "intel"
family = "alder_lake"
generation = 12
codename = "Alder Lake"

[cpu.cores]
performance = 8      # P-cores
efficiency = 4       # E-cores
threads = 20         # Total threads
base_clock_mhz = 2100
boost_clock_mhz = 4900

[cpu.isa]
avx2 = true
avx512 = false       # Disabled on desktop Alder Lake
fma3 = true
vnni = true

[cpu.memory]
max_speed_mhz = 4800   # DDR5 or 3200 DDR4
channels = 2
max_capacity_gb = 128

[tier]
classification = 4
reason = "12th gen hybrid architecture, AVX2, DDR4-3200/DDR5 support"

[performance]
p_core_pinning = true
p_core_range = "0-15"
e_core_range = "16-19"
ollama_max_models = 2
ollama_num_parallel = 1
flash_attention = true

[models]
# RAM-dependent recommendations
[models.16gb]
primary = "qwen2.5-coder:7b"
fast = "qwen2.5:3b"

[models.32gb]
primary = "qwen2.5-coder:14b"
fast = "qwen2.5-coder:7b"
quick = "qwen2.5:3b"

[models.64gb]
primary = "qwen2.5-coder:32b"
fast = "qwen3.6:35b-a3b"
balanced = "qwen2.5-coder:14b"
quick = "qwen2.5:3b"

[models.128gb]
primary = "qwen2.5-coder:32b"
fast = "qwen3.6:35b-a3b"
secondary = "qwen2.5-coder:14b"
quick = "qwen2.5:3b"

[notes]
text = "Lenovo M80s Gen 3 uses this CPU. Excellent MoE specialist."
common_systems = ["Lenovo M80s Gen 3", "Dell OptiPlex 7010 Plus", "HP EliteDesk 800 G9"]
```

AMD profiles use the same table shape minus `[cpu.cores].performance`/`efficiency`
(no hybrid split) plus the optional `[cpu.topology].ccd_count` field §3.4 introduces —
see the five worked examples there and the standalone files under
`limpet/hardware-book/amd/` (§3.4.1).

### GPU Profile Format

```toml
# /etc/limpet/hardware-book/gpu/rtx-3060.toml

[gpu]
name = "NVIDIA GeForce RTX 3060"
vendor = "nvidia"
architecture = "Ampere"
vram_gb = 12
vram_type = "GDDR6"
compute_capability = "8.6"

[gpu.driver]
package = "nvidia-driver-560"
cuda_version = "12.x"
rocm = false

[gpu.inference]
max_model_layers = "all"     # At 12GB VRAM
recommended_q4_max_params = "14b"  # Fully offloaded
partial_offload_max_params = "32b"  # Split CPU+GPU

[gpu.notes]
text = "Excellent value for Limpet. 12GB handles 14B fully offloaded."
```

---

## 5. Tier Detection Algorithm

```python
#!/usr/bin/env python3
"""Limpet hardware tier classification."""

import subprocess, json, os, re

def detect_tier():
    cpu_info = get_cpu_info()
    mem_info = get_memory_info()
    gpu_info = get_gpu_info()

    # Step 1: Check for discrete GPU → Tier 5
    if gpu_info['has_dgpu']:
        return 5, f"Discrete GPU: {gpu_info['name']} ({gpu_info['vram_gb']}GB)"

    # Step 2: Check AVX2 → no AVX2 = Tier 1
    if not cpu_info['avx2']:
        return 1, "No AVX2 support (scalar GGML fallback)"

    # Step 3: Check for hybrid architecture (Intel P+E only) → Tier 4
    if cpu_info['vendor'] == 'intel' and cpu_info['has_e_cores']:
        return 4, f"Hybrid P+E cores ({cpu_info['p_cores']}P+{cpu_info['e_cores']}E)"

    # Step 3b: AMD has no P/E split (§3.9) — Zen 4/5 AVX-512 parts land in Tier 4
    # on ISA instead, and get a CCD/NUMA topology tag attached for tuning, not tiering.
    if cpu_info['vendor'] == 'amd' and cpu_info.get('avx512'):
        topology = detect_amd_topology()  # §3.9
        return 4, (
            f"Zen 4/5 AVX-512 ({topology['ccd_count']} CCD, "
            f"{topology['numa_node_count']} NUMA node(s))"
        )

    # Step 3c: AMD family 17h Zen 1/Zen+ → Tier 2 per §3.1, regardless of bandwidth.
    # Family 17h spans Zen 1 through Zen 2; model distinguishes them (AMD CPUID,
    # cross-checked against the Linux kernel's amd.c model tables): Zen 1 is model
    # 0x01 (Summit Ridge/Naples), 0x11 (Raven Ridge), 0x20 (Dali); Zen+ is 0x08
    # (Pinnacle Ridge), 0x18 (Picasso). Zen 2 starts at model 0x31 (Rome) and every
    # later family-17h part (Renoir/Lucienne/Matisse/Van Gogh/Mendocino) stays
    # >=0x30, so model < 0x30 is a safe Zen-1/Zen+ cutoff within family 17h. [D]
    # Dual-channel DDR4 on real Zen 1/Zen+ boards is always >=30 GB/s, so without
    # this branch Step 4 never catches them and they fall through to Tier 3.
    if (cpu_info['vendor'] == 'amd' and cpu_info.get('family') == 0x17
            and cpu_info.get('model', 0xFF) < 0x30):
        return 2, "Zen 1/Zen+ (half-width AVX2 decode; §3.1 Tier 2 floor)"

    # Step 4: Check memory bandwidth → <30 GB/s = Tier 2
    bandwidth = mem_info['channels'] * mem_info['speed_mhz'] * 8 / 1000  # GB/s approx
    if bandwidth < 30:
        return 2, f"Limited bandwidth (~{bandwidth:.0f} GB/s)"

    # Step 5: Default → Tier 3
    return 3, f"AVX2 + good bandwidth (~{bandwidth:.0f} GB/s)"
```

---

## 6. Profile Lookup System (Three-Tier)

### 6.1 Local Book (ships on ISO)

```
/etc/limpet/hardware-book/
├── intel/
│   ├── i3-8100.toml
│   ├── i5-8250u.toml
│   ├── i5-10400.toml
│   ├── i7-12700.toml
│   ├── xeon-e5-2670.toml
│   └── ...  (~150 Intel profiles)
├── amd/
│   ├── ryzen-5-3600.toml
│   ├── ryzen-7-5800x.toml
│   ├── ryzen-9-7950x.toml
│   ├── threadripper-7980x.toml
│   ├── epyc-9554.toml
│   └── ...  (~45 more AMD profiles — the 5 above are validated per §3.4, in-repo at `limpet/hardware-book/amd/`)
├── gpu/
│   ├── rtx-3060.toml
│   ├── rtx-4090.toml
│   ├── rx-7900xtx.toml
│   └── ...  (~30 GPU profiles)
└── index.json           # CPU model name → TOML file mapping
```

**Lookup process:**
1. Parse `/proc/cpuinfo` for model name string
2. Normalize: lowercase, strip "@ X.XXGHz", collapse whitespace
3. Search `index.json` for exact match
4. If no match: try fuzzy match (Levenshtein distance < 3)
5. If still no match: fall back to network lookup

### 6.2 Network Lookup (GitHub-hosted)

```
GET https://raw.githubusercontent.com/limpet-project/hardware-book/main/profiles/{vendor}/{normalized-name}.toml
```

- Community-contributed profiles via pull request
- Cached locally after first fetch: `/var/cache/limpet/hardware-book/`
- Cache TTL: 30 days
- Fallback if network unavailable: proceed to manual config

### 6.3 Manual Config

If no profile found (local or network), the installer presents a manual configuration screen:
```
  CPU not found in hardware book.

  Detected: "AMD Ryzen 9 9950X"
  Flags: avx2 avx512 fma3

  Please confirm or adjust:
    Tier:     [4 — Optimal         ▼]
    P-cores:  [16                   ]
    E-cores:  [0                    ]
    Threads:  [32                   ]

  The system detected AVX-512 and classified as Tier 4.
  Adjust if this seems incorrect.
```

On an AMD vendor string this screen swaps the `P-cores`/`E-cores` fields for
`CCDs`/`NUMA nodes` (from §3.9's `detect_amd_topology()`), since AMD has nothing to
put in a P-core/E-core field (§2 Tier 4 note).

- Auto-fills best guess from instruction set flags
- User can override tier and core counts
- Saves profile to `/etc/limpet/hardware-book/custom/` for future boots

---

## 7. RAM-Aware Model Selection Matrix

| RAM | Max Model Size | Recommended Primary | Max Loaded Models |
|-----|---------------|--------------------|--------------------|
| 8GB | 3B Q4 | qwen2.5:3b | 1 |
| 16GB | 7B Q4 | qwen2.5-coder:7b | 1 |
| 32GB | 14B Q4 | qwen2.5-coder:14b | 1-2 |
| 64GB | 32B Q4 | qwen2.5-coder:32b | 2 |
| 128GB | 72B Q4 | qwen2.5-coder:72b (if Tier 4+) | 2-3 |
| 256GB | Multiple 70B+ | Multiple large models | 3-4 |
| 512GB+ | Everything | Unlimited | 4+ |

**Selection algorithm:**
```python
def select_models(ram_gb, tier):
    # Reserve 4GB for OS + tools + MCP servers
    available = ram_gb - 4

    # Model sizes (Q4_K_M quantization, approximate)
    SIZES = {
        '3b': 2, '7b': 4.5, '14b': 9, '32b': 19,
        '35b-moe': 19, '72b': 42, '110b': 65
    }

    # Pick largest model that fits with 20% headroom
    usable = available * 0.8
    primary = max((s, n) for n, s in SIZES.items() if s <= usable)

    # Add fast model if room for two
    remaining = usable - primary[0]
    fast = max((s, n) for n, s in SIZES.items() if s <= remaining) if remaining > 2 else None

    return {'primary': primary[1], 'fast': fast[1] if fast else None}
```

---

## 8. Integration Points

- **Install Wizard (Step 5-6):** Calls tier detection, shows results, applies tuning.
- **HAL:** Hardware book profiles also describe USB device expectations per tier.
- **MCP Meta-Server:** Exposes `limpet_hardware_tier` tool returning current classification.
- **Agent Chat:** Shows tier badge in status bar. Model recommendations in settings.
- **LiteLLM Config:** Model aliases auto-configured based on tier + RAM.
- **Boot Pre-Check:** Re-scans hardware on every boot. If tier changes (RAM added, GPU swapped), reconfigures automatically and notifies user.
- **Settings panel (`limpet-config`/`limpet-settings`):** §3.10's advanced knobs surface through the existing hardware-tier domain (`docs/settings-spec.md` §4.2), not a new config surface.
