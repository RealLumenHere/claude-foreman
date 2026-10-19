# wq#4425 — EVALUATE: FLUX-3-Image-Klein GGUF — local image generation for Freedom Bot faces and diagrams

**VERDICT: CONDITIONAL ADOPT — build it, but only on the RTX 3090 host, and only after a
new net-new ComfyUI stack and a gated-license HF token are in place. Do NOT target BC-250
(hapi-1): its gfx1013 GPU has no ROCm userspace support (fleet's own confirmed finding from
the Hapi bring-up work) and the only backend that avoids ROCm (Vulkan, via
`stable-diffusion.cpp`) has no confirmed support yet for FLUX 3's new tensor layout. hapi-1
is also still on the bench awaiting thermal pads, so the question is moot today regardless
of software readiness.**

## 1. The model is real and the ticket's framing checks out

`fleet-search web` (SearXNG mostly rate-limited this session; DeepSeek fallback used)
confirmed `realrebelai/FLUX-3-Image-Klein_GGUFs` on Hugging Face: community GGUF
quantizations of Black Forest Labs' `flux-3-image-klein-comfy`, tagged `text-to-image`,
`image-to-image`, `comfyui`, `low-vram`. Pulled the repo's file listing directly via the HF
API (`curl .../api/models/realrebelai/FLUX-3-Image-Klein_GGUFs`, no auth needed — this repo
is public):

- Two variants, **Base** (`flux3_image_klein-<QUANT>.gguf`) and **Step-Distilled**
  (`flux3_image_klein_sd-<QUANT>.gguf`), each shipped as `Q8_0`, `Q6_K`, `Q5_K`/`Q5_K_M`/
  `Q5_K_S`, `Q4_K`/`Q4_K_M`/`Q4_K_S`, `Q4_0`. `Q3_K_*`/`Q2_K` are explicitly absent — "not
  supported by the converter used for this ladder" (repo README).
- Measured file sizes via the blob API: `Q4_0`/`Q4_K_M` = 3.37 GB (Base) / 3.39 GB
  (Distilled); `Q8_0` = 6.05 GB (Base) / 6.07 GB (Distilled).

## 2. Runtime requirement: ComfyUI + GGUF node, not llama.cpp

The repo README is explicit: "Use an up-to-date ComfyUI build with FLUX 3 support and the
[ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) custom node or another GGUF loader
supporting FLUX 3." That is a PyTorch runtime (CUDA or ROCm backend), **not** llama.cpp. It
also needs two companion files that are not in the GGUF repo itself, pulled instead from the
*gated* official checkpoint repo `black-forest-labs/flux-3-image-klein-comfy`:

- `qwen3vl_4b_int8_convrot.safetensors` (shared text encoder) — 4.86 GB, confirmed via the
  official repo's blob API.
- `flux2-vae.safetensors` (shared VAE) — 0.336 GB.
- An optional 9B prompt-upsampler the README itself calls "demanding on low-VRAM systems...
  bypass it for initial model testing if necessary."

`curl .../black-forest-labs/flux-3-image-klein-comfy/raw/main/LICENSE.md` returned "Access
to model ... is restricted. You must have access to it and be authenticated to access it" —
confirming this official repo is gated behind an HF account click-through, unlike the
`realrebelai` GGUF mirror. No prior `black-forest-labs` or `HF_TOKEN` usage anywhere in
`/home/lumen/kb/ISSUE-LOG.md` (checked), so the fleet does not already hold access. Getting
it is a one-time account action (accept BFL's license on an HF account, mint a read token)
— a physical/account task to queue, not a blocker for this eval, and not something a
headless worker can self-grant per the mask-before-print/credential-minting constraints.

Raw-weight VRAM for the cheapest usable combination (Base, Q4_0, no upsampler): 3.37 GB
(diffusion) + 4.86 GB (text encoder) + 0.34 GB (VAE) = **8.57 GB** before PyTorch/CUDA
runtime overhead and activation memory. The `Q8_0` combination is 11.25 GB raw. Neither
number is close to tight on a 24 GB card.

## 3. BC-250 (hapi-1): ROCm is a dead end here, Vulkan is unconfirmed, and the board isn't live

`ISSUE-LOG.md` row 1236 (wq#3651) already settled the ROCm question for this exact chip,
independently via two sources (the wq#3651 ticket's own pre-dispatch note and
`ops/wq1337-hapi-bringup/RUNBOOK.md` paragraphs 206-224, verified under wq#1349): **gfx1013
(Cyan Skillfish) has no ROCm userspace support, and forcing it via
`HSA_OVERRIDE_GFX_VERSION` corrupts compute results.** The fleet's own verified working path
for that board is llama.cpp over **Vulkan**, not ROCm/Ollama. ComfyUI + ComfyUI-GGUF has no
Vulkan backend — it's PyTorch-only — so that path is closed on hapi-1 regardless of bring-up
status.

The one route that could avoid ROCm is `stable-diffusion.cpp` (the `ggml`/llama.cpp-sibling
project for diffusion models), which does carry a merged Vulkan backend and does support
FLUX models in GGUF format (`fleet-search web` surfaced PR #291 adding the Vulkan backend,
with a live log loading `flux1-schnell.q2_k.gguf`). But that log is **FLUX.1**, and this
model card is explicit that FLUX 3 uses a new tensor-name layout, not the older FLUX
architecture ("tensor names are preserved rather than renamed to an older FLUX architecture
layout" — repo README, conversion-notes section). I found no evidence `stable-diffusion.cpp`
has added FLUX 3 architecture detection; absence of a hit is not proof it doesn't exist, but
it means this path is unconfirmed, not "it'll just work."

Separately, and independent of all software questions: hapi-1 (BC-250) has been sitting on
the bench awaiting thermal pads since 2026-09-02 per `lumen_state hapi-1.bringup_eta` (same
status cited in wq#3651's own re-verify note) — there is no SSH-reachable BC-250 card today
to target even if the Vulkan path were confirmed.

## 4. RTX 3090: the only path that's both live and software-confirmed today

24 GB of dedicated VRAM, full CUDA/PyTorch support — no backend question at all. The
Q4_0/Q8_0 raw-weight totals above (8.6-11.3 GB) leave 13-15 GB of headroom for PyTorch
overhead, latents, and batch-of-one generation at typical resolutions. ComfyUI itself is net
new to this fleet (zero hits for "comfyui" anywhere in `ISSUE-LOG.md`), so the adoption cost
is a full ComfyUI install, not just a model drop-in.

## 5. Recommendation

- **File a BUILD ticket**, scoped to the RTX 3090 host only: install ComfyUI +
  `ComfyUI-GGUF`, pull `Q4_K_M` Base and Step-Distilled GGUFs from `realrebelai` (public, no
  auth), queue the one-time physical/account task of accepting BFL's gated license on an HF
  account to pull the two required companion files, and smoke-test both the text-to-image
  and image-editing official workflow templates (linked in the repo README) against a
  Freedom Bot face sprite and a Miko diagram asset.
- **Do not scope any part of that ticket to BC-250/hapi-1.** Revisit only after (a) hapi-1
  is physically bench-live, and (b) `stable-diffusion.cpp` (or another Vulkan-backed GGUF
  diffusion runtime) confirms FLUX 3 architecture support — neither condition holds today.

## 6. Evidence trail

- Repo + file listing: `curl https://huggingface.co/api/models/realrebelai/FLUX-3-Image-Klein_GGUFs` and `?blobs=true` (sizes above), no auth required — this session, 2026-10-19.
- Companion-file sizes and gating: `curl https://huggingface.co/api/models/black-forest-labs/flux-3-image-klein-comfy?blobs=true` (sizes) and `curl .../LICENSE.md` → `"Access to model ... is restricted"` — this session.
- README conversion-notes and ComfyUI setup text: `curl https://huggingface.co/realrebelai/FLUX-3-Image-Klein_GGUFs/raw/main/README.md`.
- gfx1013/ROCm finding: `/home/lumen/kb/ISSUE-LOG.md:1385` (row 1236, wq#3651), cross-referencing `ops/wq1337-hapi-bringup/RUNBOOK.md` paragraphs 206-224 (verified wq#1349) and the BC-250 hardware specs (16 GB unified GDDR6, Cyan Skillfish/gfx1013, 24 CUs) from `fleet-search web "BC-250 PS5 APU VRAM GDDR6 ROCm specs"` → Zenodo preprint.
- `stable-diffusion.cpp` Vulkan + FLUX GGUF support: `fleet-search web "stable-diffusion.cpp FLUX GGUF Vulkan backend support"` → `rmatif/stable-diffusion.cpp` README and PR #291 log (FLUX.1-schnell, not confirmed for FLUX 3).
- hapi-1 bring-up status: `/home/lumen/kb/ISSUE-LOG.md` row 1236 (wq#3651) re-citing `lumen_state hapi-1.bringup_eta` (unchanged since 2026-09-02).
- No prior ComfyUI/BFL/HF_TOKEN usage: `grep -in "comfyui" /home/lumen/kb/ISSUE-LOG.md` and `grep -in "black-forest-labs\|HF_TOKEN" /home/lumen/kb/ISSUE-LOG.md` — both zero hits, this session.
