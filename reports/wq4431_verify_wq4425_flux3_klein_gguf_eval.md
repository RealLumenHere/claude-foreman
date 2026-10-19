# wq#4431: verify-pass on wq#4425's FLUX-3-Image-Klein GGUF eval doc (commit `eebe9508`) — CONTRADICT

Author: claude-sonnet-5. Verifier: claude-sonnet-5@high (fresh instance, no prior context on
wq#4425 beyond the ticket text and the shipped doc).

## What wq#4425 shipped

One new file, `research/WQ4425_FLUX3_KLEIN_GGUF_EVAL.md` (109 lines, no code), commit
`eebe9508`. Verdict: **CONDITIONAL ADOPT** — build on the RTX 3090 host only; explicitly
**do not** target BC-250/hapi-1 (gfx1013), on two independent grounds: (a) ROCm has no
userspace support for gfx1013 (correctly cross-cited to `ISSUE-LOG.md` row 1236/wq#3651),
and (b) the one ROCm-avoiding path, `stable-diffusion.cpp`'s Vulkan backend, is claimed to
have "no confirmed support yet for FLUX 3's new tensor layout" — stated as "unconfirmed,
not 'it'll just work.'" hapi-1 is also independently bench-down (thermal pads since
2026-09-02), so the doc treats the question as doubly moot.

## Independent re-derivation (cold, not trusting the diff)

**HF repo + file-size claims (§1):** re-pulled
`https://huggingface.co/api/models/realrebelai/FLUX-3-Image-Klein_GGUFs?blobs=true` myself.
Confirmed: public, `gated: false`, Base+Step-Distilled variants, quantization ladder exactly
as described (`Q8_0,Q6_K,Q5_K/_M/_S,Q4_K/_M/_S,Q4_0`, no `Q3_K_*`/`Q2_K`). File sizes match
to the byte-rounded GB the doc cites: Base `Q4_0`=3,371,504,288 B (3.37 GB), Distilled
`sd-Q4_0`=3,391,951,648 B (3.39 GB), Base `Q8_0`=6,051,664,544 B (6.05 GB), Distilled
`sd-Q8_0`=6,072,111,904 B (6.07 GB). All correct.

One thing the doc doesn't mention, noted but not a contradiction: each variant folder also
ships a `..._w4a8_convrot.safetensors` (~3.5 GB), a non-GGUF alternate quantization of the
diffusion model not covered by the README's GGUF-ladder table. Doesn't affect the doc's
VRAM math or verdict since it's a parallel format, not a missing dependency.

**Companion-file gating (§2):** re-pulled
`https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy?blobs=true` and
`.../LICENSE.md` myself. `LICENSE.md` returns HTTP 401 with `x-error-code: GatedRepo` —
matches the doc's quote exactly. Companion file sizes match exactly:
`qwen3vl_4b_int8_convrot.safetensors`=4,858,559,240 B (4.86 GB),
`flux2-vae.safetensors`=336,211,292 B (0.336 GB). VRAM arithmetic (8.57 GB / 11.25 GB raw)
re-checks correctly. The optional-upsampler quote ("demanding on low-VRAM systems... bypass
it for initial model testing if necessary") is verbatim from the real README. Confirmed.

**ISSUE-LOG cross-checks (§3, §6):** `grep -in "comfyui" ISSUE-LOG.md` → 0 hits;
`grep -in "black-forest-labs\|HF_TOKEN" ISSUE-LOG.md` → 0 hits. Matches the doc's claimed
"zero hits" searches. Row 1236 (wq#3651) does state gfx1013/Cyan Skillfish has no ROCm
userspace support and that hapi-1 has been bench-down for thermal pads since 2026-09-02 —
matches the doc's citation and framing exactly.

**The load-bearing claim that does NOT hold: `stable-diffusion.cpp` FLUX 3 support (§3,
evidence item 5).** The doc says: *"I found no evidence `stable-diffusion.cpp` has added
FLUX 3 architecture detection; absence of a hit is not proof it doesn't exist, but it means
this path is unconfirmed, not 'it'll just work.'"* This is false, and was checkable at
research time with the same kind of query the doc already ran for the Vulkan-backend PR:

- `github.com/leejet/stable-diffusion.cpp` (the real upstream the doc's own cited fork,
  `rmatif/stable-diffusion.cpp`, descends from — confirmed via GitHub API `parent` field)
  merged **PR #2143, "feat: add FLUX.3 Image Klein and Klein Base support"**, authored by
  the repo owner `leejet` himself, merged to `master` **2026-10-15T18:42:07Z — four days
  before this eval doc was written** (doc committed `eebe9508`, 2026-10-19). CodeRabbit's
  own PR summary:
  "Added support for FLUX.3 Image Klein and Klein Base, including model detection, prompt
  handling, image generation, and reference-image editing."
- The project's live `README.md` news banner (first entry, line 19) reads: *"2026/10/16 🚀
  stable-diffusion.cpp adds Day-0 support for FLUX.3 Image Klein / Klein Base"* — dated
  three days before the doc's research session, in the single most obvious place to check
  (the README banner, not a buried issue).
- `docs/flux3.md` on the same repo links `leejet`'s own pre-converted GGUF mirrors
  (`huggingface.co/leejet/flux-3-image-klein-GGUF`,
  `.../flux-3-image-klein-base-GGUF`) and a working `sd-cli` invocation using a
  **GGUF-quantized text encoder** (`Qwen3-VL-4B-Instruct-Q4_K_M.gguf`) — a fully
  llama.cpp-sibling, non-PyTorch, non-ComfyUI pipeline for exactly this model.
- The doc's own evidence item 5 already confirms the Vulkan backend (PR #291, merged,
  `rmatif/stable-diffusion.cpp`) lives in this same codebase. PR #2143 (FLUX 3 architecture
  detection) and PR #291 (Vulkan backend) are both merged to the same `master` with no
  indication of mutual exclusivity — architecture detection and compute-backend dispatch are
  orthogonal in this codebase (the README lists Vulkan as a general backend, not
  per-architecture).

This is load-bearing: the doc's §3/§5 recommendation to defer BC-250/hapi-1 names exactly
two conditions for revisiting — "(a) hapi-1 is physically bench-live, and (b)
`stable-diffusion.cpp` ... confirms FLUX 3 architecture support — neither condition holds
today." Condition (b) in fact already held four days before the doc was written. The
hardware blocker (a, hapi-1 bench-down for thermal pads) independently still holds and is
correctly verified above, so the **bottom-line "don't build on BC-250 today" recommendation
survives** — but the doc's own stated software-readiness reasoning for it is stale/wrong,
and a reader following its "revisit when" criteria would wrongly keep waiting on condition
(b) after it was already satisfied.

## Probe

Research-only ticket (`wq show 4425`: no code shipped). No acceptance probe to run beyond
re-deriving the doc's own citations, which is what this pass did.

## Verdict

**CONTRADICT.** All Hugging Face file-listing, file-size, and gating claims (§1, §2)
independently re-verified byte-exact against primary sources. ISSUE-LOG citations (§3, §6)
verified verbatim. The bottom-line recommendation (adopt on RTX 3090, don't build on
BC-250/hapi-1 *today*) is not overturned — the hardware blocker on hapi-1 is real and
independently sufficient. But the doc's claim that `stable-diffusion.cpp` lacks FLUX 3
architecture detection, used as supporting technical reasoning and as the doc's own
stated unblock condition for revisiting BC-250, is factually wrong: FLUX 3 Klein/Klein-Base
support merged to the project's `master` on 2026-10-15 (PR #2143, `8cd731e7`), four days
before this research session, advertised in the README's first news-banner line the next
day (2026-10-16). Per archetype 8, escalating to opus-5@high for judgment.

**Post-judgment addendum:** the escalated opus-5@high judgment (CONFIRMED the
contradiction; see `reports/wq4431_judgment_wq4425_flux3_klein_gguf_eval.md`) caught a
transcription slip in this report's first draft — the PR merge timestamp above was
originally mistyped as `2026-10-19` instead of the API's actual `2026-10-15`; corrected
here. The judgment also found the residual Vulkan-on-gfx1013 risk is real (adjacent
FLUX.2-klein Vulkan bugs open on AMD shared-memory hardware, #1718/#1665) even though the
architecture-support gap itself is closed, and found the doc's "ROCm is closed on hapi-1
regardless" framing overstates an experimental ROCm lane recorded in
`agents/work-queue/reports/425_hapi1_bringup_runbook.md` §5. Corrections applied to
`research/WQ4425_FLUX3_KLEIN_GGUF_EVAL.md` per the judgment's item 4; methodology lesson
logged at `ISSUE-LOG.md` row 1485.

## Evidence trail (this pass)

- `curl https://huggingface.co/api/models/realrebelai/FLUX-3-Image-Klein_GGUFs?blobs=true` — file listing/sizes, this session.
- `curl https://huggingface.co/api/models/black-forest-labs/flux-3-image-klein-comfy?blobs=true` and `curl -i .../LICENSE.md` — companion sizes + 401 GatedRepo, this session.
- `curl https://huggingface.co/realrebelai/FLUX-3-Image-Klein_GGUFs/raw/main/README.md` — ladder table, ComfyUI setup, companion-file list, upsampler quote, this session.
- `curl https://api.github.com/repos/rmatif/stable-diffusion.cpp` — `fork:true`, `parent:leejet/stable-diffusion.cpp`, this session.
- `curl https://api.github.com/repos/leejet/stable-diffusion.cpp/pulls/291` — Vulkan backend PR, merged, this session.
- `curl https://api.github.com/search/issues?q=repo:leejet/stable-diffusion.cpp+flux3` → PR #2143 + issue #2142, this session.
- `curl https://api.github.com/repos/leejet/stable-diffusion.cpp/pulls/2143` — merged `2026-10-15T18:42:07Z`, author `leejet`, body/CodeRabbit summary, this session.
- `curl https://raw.githubusercontent.com/leejet/stable-diffusion.cpp/master/README.md` — line 19 news banner, line 48 doc index, line 99 Vulkan backend list, this session.
- `curl https://raw.githubusercontent.com/leejet/stable-diffusion.cpp/master/docs/flux3.md` — GGUF mirrors + `sd-cli` invocation, this session.
- `grep -in "comfyui" /home/lumen/kb/ISSUE-LOG.md` and `grep -in "black-forest-labs\|HF_TOKEN" /home/lumen/kb/ISSUE-LOG.md` — both 0 hits, this session.
- `/home/lumen/kb/ISSUE-LOG.md` row 1236 (wq#3651) — read directly, this session.
