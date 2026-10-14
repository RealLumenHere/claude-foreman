# wq#3838 — verify-pass of wq#3835 AMD CPU tier research (PR#1830)

**Verdict: CONFIRMED.** wq#3835's doc (`research/WQ3835_AMD_CPU_TIER_RESEARCH.md`,
merged `d12295789c`) is a research deliverable, not code — there is no runnable
acceptance probe in the code-change sense. "Running its probe" here means
independently reproducing every falsifiable claim the doc makes: the degraded
research-council run, the three dead prior-art search engines, and the fleet-internal
citations it leans on. All of that reproduced cleanly. Two minor findings surfaced
(below); neither contradicts the doc's conclusions or its tier ladder.

## What wq#3835 claimed

AMD CPU/APU tier ladder for the Limpet installer (FX/Bulldozer → Zen 1-4 →
Threadripper → BC-250), sourced from a degraded 1/3-seat `research-council` run
(only `deepseek-v4-pro` answered; `gemini-flash` 429'd, `cerebras-oss-120b` 402'd)
plus fleet-internal measured BC-250 data and the existing `fleet_hardware.json`
ROCm-dead note. Every DeepSeek-sourced number is explicitly flagged non-measured,
confidence-graded, and the doc's own "Open items" section says not to ship a numeric
promise before benchmarking real hardware.

## What I independently reproduced

- **ISSUE-LOG row 1296** (cited for the degraded council run): read
  `kb/ISSUE-LOG.md:1445` directly — symptom, cause, and resolution text match the
  doc's narration (gemini-flash 429 quota, cerebras-oss-120b 402 billing, 2 runs
  ~3 min apart, deepseek-v4-pro answered both times) word-for-word on the specifics
  (429/402 codes, timing, seat names). Not a paraphrase that drifted from the row.
- **Prior-art dead-end claims, live-reproduced, not trusted from the doc**:
  - `fleet-search web "llama.cpp AVX2 bandwidth"` → `ok:false`, all four engines
    suspended/CAPTCHA'd, fallback failure names the exact missing `BRAVE_API_KEY`
    (wq#2595) the doc cites.
  - `fleet-search github "llama.cpp"` → 401, "no usable token" — matches the doc's
    "401, no usable token" claim exactly.
  - `fleet-search reddit "llama.cpp"` → `ok:true`, empty `results`, consistent with
    the already-logged wq#2568/wq#2601 stale-cache-on-outage bug the doc cites rather
    than re-logging.
  - All three are genuinely dead right now, not a convenient excuse to skip research.
- **Fleet-internal citations checked against source, not taken on faith**:
  - `research/fleet_hardware.json:27` — ROCm-dead note on BC-250 gfx1013 quoted
    verbatim correctly.
  - `research/fleet_hardware.json:369` — "AMD Cyan Skillfish APU: Zen 2, 6C/12T"
    matches the doc's BC-250 CPU claim exactly.
  - `research/fleet_hardware.json:30` — nephthys FirePro D500 Vulkan tier reference
    checks out.
  - `agents/work-queue/reports/bc250-llamacpp-vulkan-rocm-20260709.md` — confirms
    Vulkan-only verdict, 16GB unified pool, and the ~37.5 tok/s 35B-A3B Q4 figure.
  - `agents/work-queue/reports/wq109_zendnn_horus_2026-07-13.md:32-33` — "memory
    bandwidth, not core count" / bandwidth-bound MoE claim present and correctly
    paraphrased (not an exact quote, but faithful to the source's point).
  - `agents/work-queue/reports/hapi-blade-coding-model-20260709.md` — 66/87.5 tok/s
    gen, 181/248 tok/s prefill figures for gpt-oss-20b on BC-250 confirmed present.
- **Bandwidth arithmetic**: DDR4-3200 dual-channel = 3200×8B×2 = 51.2 GB/s; DDR5-6000
  dual-channel = 6000×8B×2 = 96 GB/s; ratio 96/51.2 = 1.875 — matches the doc's "51.2
  → 96 GB/s = 1.875x theoretical" exactly. IF read-bandwidth arithmetic (1600 MHz ×
  32 B/cycle = 51.2 GB/s; 2000 MHz × 32 B/cycle = 64 GB/s) is also internally correct
  multiplication, though see finding 2 below.

## Findings (neither blocks CONFIRM)

1. **Minor citation misattribution.** The "~37.5 tok/s on a 35B-A3B-class MoE at Q4,
   64K ctx" figure (fleet-internal ground-truth section) is cited to
   `research/LOCAL_MODEL_STRATEGY.md:365-366` and
   `reports/hapi-blade-coding-model-20260709.md`. Neither file contains that figure —
   I grepped both for `37.5`/`35B-A3B` and found nothing. The actual source is
   `reports/bc250-llamacpp-vulkan-rocm-20260709.md:13,29` (and
   `reports/214_bc250_validation_gate.md:8`), which the doc does cite correctly
   elsewhere in the same section for the ROCm-dead claim — the citation anchor for
   this one specific number just points at the wrong file. The number itself is true
   and fleet-sourced; this is a paperwork defect, not a factual one.
2. **Unverifiable-right-now detail, already self-flagged by the author.** §6 names
   `gfx1102` as Hawk Point's (Ryzen 8040) iGPU target; my own training-data
   recollection is that gfx1102 is Navi 33 (discrete RX 7600-class), and Hawk Point's
   RDNA3 iGPU (same silicon generation as Phoenix) is more likely `gfx1103` or a
   newer `gfx115x` ID depending on ROCm version — but `fleet-search web`/`github` are
   both genuinely down right now (confirmed above), so I can't check this against a
   live source, and the doc's own text already says "the exact unsupported-gfx list
   is the less-certain part" of an otherwise very-high-confidence claim. Flagging as
   PLAUSIBLE, not CONFIRMED-wrong — re-check the specific gfx ID once `fleet-search
   web`/`github` recover (wq#2595 / github token), it doesn't change the
   Vulkan-over-ROCm verdict either way.

## Why this doesn't escalate

Both findings are below the bar for CONTRADICT: finding 1 is a citation-anchor slip
on a fact that is true and sourced elsewhere in the fleet; finding 2 is an
already-disclosed uncertainty in the source doc that I could not independently
verify (search engines down) rather than a reproduced factual error. The doc's core
deliverable — an AVX2-gated, DDR-generation-bucketed tier ladder, Vulkan-over-ROCm
guidance for APUs, and BC-250 placed by GPU bandwidth not CPU generation — is
correctly grounded in fleet-internal evidence I independently re-checked, and the
DeepSeek-sourced numbers are honestly labeled as unverified estimates throughout.
