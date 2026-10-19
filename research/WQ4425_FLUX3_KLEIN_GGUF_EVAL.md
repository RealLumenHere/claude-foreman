# wq#4425 — EVALUATE: FLUX-3-Image-Klein GGUF — local image generation for Freedom Bot faces and diagrams

**VERDICT: CONDITIONAL ADOPT — build it, but only on the RTX 3090 host, and only after a
new net-new ComfyUI stack and a gated-license HF token are in place. Do NOT target BC-250
(hapi-1) TODAY: its gfx1013 GPU has no *stock* ROCm userspace support (fleet's own
confirmed finding from the Hapi bring-up work), and the one path that could avoid ROCm
(Vulkan, via `stable-diffusion.cpp`) has FLUX 3 architecture support as of upstream
`leejet/stable-diffusion.cpp` PR #2143 (merged 2026-10-15, `8cd731e7`) — but no one has
published a FLUX 3 run specifically on the Vulkan backend, and the closest predecessor
pipeline (FLUX.2-klein) has open Vulkan bugs on AMD shared-memory hardware
(leejet/stable-diffusion.cpp#1718, #1665) that make gfx1013 risk a real open question, not
a closed one. **The decisive blocker is hardware, not software**: hapi-1 is still on the
bench awaiting thermal pads, so there is no SSH-reachable board to test either path
against regardless of software readiness.
*(Corrected 2026-10-19 per wq#4431 verify-pass + escalated opus-5@high judgment —
see §3 and §5 below and `reports/wq4431_judgment_wq4425_flux3_klein_gguf_eval.md`. The
original verdict here understated FLUX 3 Vulkan/sd.cpp support as "no confirmed support
yet for FLUX 3's new tensor layout"; that specific architecture-detection gap had already
closed upstream four days before this doc was researched.)*

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

## 3. BC-250 (hapi-1): stock ROCm is closed, Vulkan/sd.cpp has the architecture but not a
   proven run, and the board isn't live anyway

**[Corrected 2026-10-19 — wq#4431 verify-pass + opus-5@high judgment, see
`reports/wq4431_judgment_wq4425_flux3_klein_gguf_eval.md`.]**

`ISSUE-LOG.md` row 1236 (wq#3651) already settled the *stock* ROCm question for this exact
chip, independently via two sources (the wq#3651 ticket's own pre-dispatch note and
`ops/wq1337-hapi-bringup/RUNBOOK.md` paragraphs 206-224, verified under wq#1349): **gfx1013
(Cyan Skillfish) has no stock ROCm userspace support, and forcing it via
`HSA_OVERRIDE_GFX_VERSION` corrupts compute results.** Note this doc originally said "ROCm
is a dead end" without qualification; `agents/work-queue/reports/425_hapi1_bringup_runbook.md`
§5 records a bleeding-stack ROCm/PyTorch *experiment* lane (kernel 7.1.5 + TLB-flush fix +
HW scheduling + 40CU unlock, external community report by akandr, not yet reproduced by the
fleet) that is real but still not a production inference path — "closed regardless of
bring-up status" overstated the case. The fleet's own verified *production* path for that
board remains llama.cpp over **Vulkan**, not ROCm/Ollama. ComfyUI + ComfyUI-GGUF has no
Vulkan backend — it's PyTorch-only — so the ComfyUI path specifically is closed on hapi-1.

The one route that could avoid ROCm is `stable-diffusion.cpp` (the `ggml`/llama.cpp-sibling
project for diffusion models). This doc originally cited a *fork*
(`rmatif/stable-diffusion.cpp`) for its merged Vulkan backend (PR #291) and concluded FLUX 3
architecture support was unconfirmed. That conclusion was wrong: the fork's own upstream,
`github.com/leejet/stable-diffusion.cpp`, merged **PR #2143, "feat: add FLUX.3 Image Klein
and Klein Base support," on 2026-10-15 (`8cd731e7`)** — authored by the repo owner, merged
four days before this doc's research session, and announced as the first line of the
README's news banner the next day (2026-10-16). Official GGUF mirrors
(`leejet/flux-3-image-klein-GGUF`, `...-base-GGUF`) went public 2026-10-18. The
architecture-detection gap this doc flagged as the §5 unblock condition was already closed.

What remains genuinely unconfirmed: **a FLUX 3 run specifically on the Vulkan backend.**
PR #2143 adds no backend-specific code (it builds its graph from generic `ggml` ops and
takes an opaque `ggml_backend_t`), so it should dispatch to Vulkan the same way FLUX.1/2 do
— but nobody has published a FLUX 3 Vulkan run, and the closest predecessor pipeline
(FLUX.2-klein, sharing FLUX 3's VAE family and Qwen3 text-encoder family) has open Vulkan
bugs specifically on AMD shared-memory hardware: `leejet/stable-diffusion.cpp#1718`
("Vulkan buffer-size OOM on Ryzen AI Max+/Strix Halo during Qwen3 conditioning") and `#1665`
("Vulkan img2img returns white image"). gfx1013's only proven Vulkan workload today is
llama.cpp LLM inference, which never exercises the conv2d/im2col VAE paths a diffusion run
needs. So "unconfirmed on Vulkan, not 'it'll just work'" is still the right framing — just
for a narrower, real reason (no Vulkan run published, adjacent-pipeline bugs on this GPU
class) instead of a missing architecture (which is no longer missing).

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
- **Do not scope any part of that ticket to BC-250/hapi-1 today.** Revisit after (a)
  hapi-1 is physically bench-live, then run (b) a concrete, falsifiable probe: `sd-cli
  --backend diffusion=vulkan0` (per `docs/backend.md`'s backend-placement syntax) loading a
  FLUX 3 Klein Q4 GGUF from `leejet/flux-3-image-klein-GGUF` for one text-to-image and one
  `-r` image-edit run on hapi-1's RADV/gfx1013, checked for correct (non-garbage,
  non-white) output — not merely "does it launch." *[Corrected 2026-10-19: condition (b) as
  originally written ("stable-diffusion.cpp confirms FLUX 3 architecture support") was
  already satisfied before this doc was written (PR #2143, 2026-10-15) and was not a
  condition that could still fail; only (a) blocks today.]* Pin `--backend te=cpu` as a
  first fallback if conditioning OOMs the way `leejet/stable-diffusion.cpp#1718` describes
  on other AMD shared-memory parts.
- **Follow-up ticket filed: wq#4432**, parked on `hapi-1.bringup_eta` going live — cheap,
  and closes the one real open question without holding up the 3090 BUILD ticket.
- **Scoping note for the 3090 BUILD ticket**: `stable-diffusion.cpp`'s CUDA backend now runs
  the same FLUX 3 Klein model as a single native binary (same `sd-cli` path as above, no
  ComfyUI/PyTorch install). That may be a lighter install than ComfyUI and would put the
  3090 and a future hapi-1 on one runtime. Compare `sd-cli` CUDA against ComfyUI before
  committing to ComfyUI — ComfyUI's official workflow templates remain a real advantage, and
  the BFL gated-license account task is needed on either path.

## 6. Evidence trail

- Repo + file listing: `curl https://huggingface.co/api/models/realrebelai/FLUX-3-Image-Klein_GGUFs` and `?blobs=true` (sizes above), no auth required — this session, 2026-10-19.
- Companion-file sizes and gating: `curl https://huggingface.co/api/models/black-forest-labs/flux-3-image-klein-comfy?blobs=true` (sizes) and `curl .../LICENSE.md` → `"Access to model ... is restricted"` — this session.
- README conversion-notes and ComfyUI setup text: `curl https://huggingface.co/realrebelai/FLUX-3-Image-Klein_GGUFs/raw/main/README.md`.
- gfx1013/ROCm finding: `/home/lumen/kb/ISSUE-LOG.md:1385` (row 1236, wq#3651), cross-referencing `ops/wq1337-hapi-bringup/RUNBOOK.md` paragraphs 206-224 (verified wq#1349) and the BC-250 hardware specs (16 GB unified GDDR6, Cyan Skillfish/gfx1013, 24 CUs) from `fleet-search web "BC-250 PS5 APU VRAM GDDR6 ROCm specs"` → Zenodo preprint.
- `stable-diffusion.cpp` Vulkan backend: PR #291 "Add vulkan backend" (author `sohzm`,
  merged 2024-08-27) — into `leejet/stable-diffusion.cpp` directly, *not* the fork
  (`rmatif/stable-diffusion.cpp`, confirmed via GitHub API `parent` field, is a fork of
  `leejet/stable-diffusion.cpp`; the original research session found this PR via the
  fork's README, which is where the FLUX 3 miss came from — see correction below).
  *[Corrected 2026-10-19: originally mis-attributed to the `rmatif` fork.]*
- hapi-1 bring-up status: `/home/lumen/kb/ISSUE-LOG.md` row 1236 (wq#3651) re-citing `lumen_state hapi-1.bringup_eta` (unchanged since 2026-09-02).
- **[Added 2026-10-19, wq#4431 verify-pass + judgment]** FLUX 3 architecture support in
  `stable-diffusion.cpp`: `curl https://api.github.com/repos/leejet/stable-diffusion.cpp/pulls/2143`
  (merged `2026-10-15T18:42:07Z`, `8cd731e7`); `curl
  https://raw.githubusercontent.com/leejet/stable-diffusion.cpp/master/README.md` (news
  banner line 19, dated 2026-10-16); `curl
  https://raw.githubusercontent.com/leejet/stable-diffusion.cpp/master/docs/flux3.md`
  (GGUF mirror links + `sd-cli` invocations); `curl
  https://huggingface.co/api/models/leejet/flux-3-image-klein-GGUF` (public, `createdAt
  2026-10-18`). Adjacent-pipeline Vulkan risk: `leejet/stable-diffusion.cpp` issues #1718,
  #1665 (both open). Experimental ROCm lane: `agents/work-queue/reports/425_hapi1_bringup_runbook.md`
  §5. Full evidence and analysis: `reports/wq4431_verify_wq4425_flux3_klein_gguf_eval.md`
  and `reports/wq4431_judgment_wq4425_flux3_klein_gguf_eval.md`.
- No prior ComfyUI/BFL/HF_TOKEN usage: `grep -in "comfyui" /home/lumen/kb/ISSUE-LOG.md` and `grep -in "black-forest-labs\|HF_TOKEN" /home/lumen/kb/ISSUE-LOG.md` — both zero hits, this session.
