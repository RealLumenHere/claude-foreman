# wq#4431 escalated judgment: wq#4425 (research/WQ4425_FLUX3_KLEIN_GGUF_EVAL.md) stable-diffusion.cpp FLUX 3 support claim

Judge: opus-5@high, judgment only (no patch; any correction is applied separately).
Verifier: claude-sonnet-5@high. Date: 2026-10-19.
Subject: worktree `/home/lumen/worktrees/wq4431-a1`, doc landed in `eebe9508`
(committed 2026-10-19 23:10:28 +0000), verify report
`reports/wq4431_verify_wq4425_flux3_klein_gguf_eval.md`.

## Claim under judgment

From the doc's verdict block (L5-7):

> Do NOT target BC-250 (hapi-1): its gfx1013 GPU has no ROCm userspace support [...] and the
> only backend that avoids ROCm (Vulkan, via `stable-diffusion.cpp`) has no confirmed support
> yet for FLUX 3's new tensor layout.

Restated in §3 (L72-74): *"I found no evidence `stable-diffusion.cpp` has added FLUX 3
architecture detection"*, and used in §5 (L97-99) as unblock condition (b): *"Revisit only
after (a) hapi-1 is physically bench-live, and (b) `stable-diffusion.cpp` (or another
Vulkan-backed GGUF diffusion runtime) confirms FLUX 3 architecture support — neither
condition holds today."*

The verifier says condition (b) already held when the doc was written. That would be false
if PR #2143 isn't really merged to `master`, if it landed after the research session, or if
"support" exists only in a fork or branch the doc couldn't reasonably have meant.

## Evidence

All gathered in this session; I did not reuse any of the verifier's output.

### PR #2143: real, merged to master, earlier than the verifier said
- `curl -s https://api.github.com/repos/leejet/stable-diffusion.cpp/pulls/2143` →
  `title: "feat: add FLUX.3 Image Klein and Klein Base support"`, `user: leejet`,
  `base: master`, `merged: true`, `created_at: 2026-10-15T18:25:03Z`,
  **`merged_at: 2026-10-15T18:42:07Z`**, `merge_commit_sha: 8cd731e7b4667eb8f40a153315b9a7125d09a58d`,
  15 files, +413/-6.
- `GET /repos/leejet/stable-diffusion.cpp/commits/8cd731e7…` → author `leejet`, date
  2026-10-15T18:42:07Z, message `feat: add FLUX.3 Image Klein and Klein Base support (#2143)`.
- `GET /repos/leejet/stable-diffusion.cpp/compare/8cd731e7…...master` → `status: ahead`,
  `ahead_by: 9`, `behind_by: 0`. The merge commit is an ancestor of the current `master`.
- **Correction to the verify report:** it gives the merge time as `2026-10-19T18:42:07Z`
  and later as "2026-10-16/19". The API says 2026-10-**15**, with the same time of day, so
  this looks like a transcription slip. Fixing it makes the contradiction stronger: the
  support merged about 4 days 4.5 hours before `eebe9508`, not "same day".
- Upstream issue #2142 "[Feature] FLUX.3 Image Klein support" was opened 2026-10-15T16:56Z
  and closed by this PR (`GET /search/issues?q=repo:leejet/stable-diffusion.cpp+"FLUX.3"`).

### README banner and docs
- `curl -s https://raw.githubusercontent.com/leejet/stable-diffusion.cpp/master/README.md`,
  line 19 (the second bullet under "Important News"; line 18 is a same-day MOSS-Image
  entry): `* **2026/10/16** 🚀 stable-diffusion.cpp adds **Day-0 support for FLUX.3 Image
  Klein / Klein Base**`. Lines 96-100 list Vulkan as a general backend, not tied to any
  model family.
- `docs/flux3.md` (added by #2143) links leejet's own GGUF repos and gives `sd-cli`
  invocations that use a GGUF Qwen3-VL-4B text encoder.
- `GET huggingface.co/api/models/leejet/flux-3-image-klein-GGUF` → public,
  `gated: false`, `createdAt: 2026-10-18T08:38:10Z`, ships `Q2_K`…`Q8_0`. Same result for
  `…-base-GGUF`. These loader-matched quants were public a day before the doc.
- The doc's verdict line itself is dated 2026-10-19, so the "no evidence" finding came
  after the news banner had been up for three days at the top of the upstream README.

### What #2143 actually touches (the orthogonality question)
- `GET /pulls/2143/files`: `src/model/diffusion/flux3.hpp` (new, +256),
  `model.h`, `model_loader.cpp`, `model/diffusion/model.hpp`, `pipeline/diffusion_engine.cpp`,
  `pipeline/model_builders.cpp`, `runtime/denoiser.hpp`, `conditioning/conditioner.hpp`,
  `model/te/llm.hpp`, plus docs/assets. **It changes nothing under ggml or any backend.** It
  adds no CUDA- or Vulkan-specific code and no new kernels.
- `flux3.hpp` at the merge SHA includes `model/diffusion/flux.hpp` and builds its graph from
  `ggml_concat`, `ggml_add`, `ggml_norm`, `ggml_mul`, `ggml_view_3d/4d`, `ggml_reshape_4d`,
  `ggml_ext_timestep_embedding`, and `ggml_ext_silu_act`, plus shared FLUX blocks. The
  runner takes an opaque `ggml_backend_t`. `docs/backend.md` describes backend placement
  per component (`--backend te=cpu,vae=cuda0,diffusion=vulkan0`) for "Flux … and other
  diffusion models".
- **What is still missing:** neither the PR body nor the docs show a FLUX.3 run on Vulkan.
  The PR author's examples are Windows `sd-cli.exe` runs with `--offload-to-cpu` and no
  stated backend. `GET /search/issues?q=repo:leejet/stable-diffusion.cpp+flux3+vulkan`
  returns only #2142, so no one has reported FLUX.3 on Vulkan working or failing.
- Open Vulkan bugs in the closest predecessor pipeline (FLUX.2-klein, which FLUX.3 Klein
  shares its VAE family and Qwen3-family text encoder with):
  - #1718 (open): "Vulkan buffer-size OOM on Ryzen AI Max+/Strix Halo during Qwen3
    conditioning for Flux.2-Klein". This is an AMD unified-memory part, which is the same
    class as the BC-250's 16 GB shared GDDR6.
  - #1665 (open): "Vulkan img2img returns white image" with a FLUX.2-klein GGUF. That is
    the reference-image path the fleet's planned edit workflow would use.
  - #1216 (closed 2026-01-21): FLUX.2-klein-4B failed to run under Vulkan.

### hapi-1 hardware blocker, checked independently
- `/home/lumen/kb/ISSUE-LOG.md:1385`, row 1236 (wq#3651), read directly. It states gfx1013
  has no ROCm userspace support, `HSA_OVERRIDE_GFX_VERSION` corrupts results, and hapi-1 is
  "still on the bench awaiting thermal pads (lumen_state hapi-1.bringup_eta, since
  2026-09-02)". This matches the doc's citation.
- `python3 /home/lumen/minerva/lumen_state.py history hapi-1 bringup_eta` → current value
  since 2026-09-02: "card in hand, on bench disassembled; awaiting Gelid Xtreme pads
  (2.0/1.5mm)+PTM7950 for card-1 thermal mod; … 3x Noctua NF-F12 … to buy", source "Frank
  status 2026-09-02". There have been no updates since. The fact is tagged
  `[UNVERIFIABLE — no probe]`. A live reachability check (`ping hapi-1`) needed approval
  this sandbox doesn't grant, so I could not probe the host. I accept the blocker as the
  fleet's own system-of-record status: it is consistent across two sources and has not
  been contradicted.

### Something both passes missed: the fleet's own runbook softens "ROCm is a dead end"
- `lumen_state current hapi-1` → `hapi-1.bringup_runbook = READY 2026-08-09:
  agents/work-queue/reports/425_hapi1_bringup_runbook.md — … Vulkan-only prod (ROCm now
  expt-viable)`.
- That runbook, §5 L70-75: "ROCm UPDATE (Aug 2026, akandr): ROCm compute now WORKS on a
  bleeding stack (kernel 7.1.5 + TLB-flush fix + HW scheduling + 40CU unlock +
  flush-on-unmap) with verified-correct GEMM/PyTorch/llama.cpp … => ROCm is now a valid
  EXPERIMENT lane (PyTorch on blade!), still NOT the inference path. Never
  HSA_OVERRIDE=10.3.0 on stock ROCm."
- Row 1236 is about *stock* ROCm and the HSA spoof, and it is correct about those. The
  doc's §3 conclusion that the ComfyUI/PyTorch path is "closed on hapi-1 regardless of
  bring-up status" ignores the experiment lane recorded in the fleet's own runbook. That
  lane is an external community report (akandr) the fleet has not reproduced. It changes
  nothing today, but "closed" overstates the case.

### Minor attribution slip in the doc
- `GET /repos/leejet/stable-diffusion.cpp/pulls/291` → "Add vulkan backend", author
  `sohzm`, merged 2024-08-27 into **leejet's** repo. The doc's §6 credits it to the
  `rmatif/stable-diffusion.cpp` README and PR. `GET /repos/rmatif/stable-diffusion.cpp` →
  `fork: true`, `parent: leejet/stable-diffusion.cpp`. The doc researched a fork, and that
  is likely why it missed the upstream FLUX.3 work.

### Gating, relevant to any sd.cpp path
- `GET huggingface.co/api/models/black-forest-labs/FLUX.2-dev` → `gated: "auto"`. The same
  holds for `black-forest-labs/flux-3-image-klein`. `Qwen/Qwen3-VL-4B-Instruct-GGUF` →
  `gated: false`. The two VAE sources `docs/flux3.md` points to are both BFL click-through
  gated, so the doc's one-time HF license task stands on any runtime path.

## Judgment

**CONTRADICTION CONFIRMED. The bottom-line recommendation stands, but its reasoning and
its revisit criteria are wrong.**

1. **The disputed sentence is factually false.** `stable-diffusion.cpp` upstream merged
   FLUX.3 Klein and Klein Base architecture support on 2026-10-15 (`8cd731e7`, PR #2143, by
   the repo owner). The README advertised it on 2026-10-16, and loader-matched GGUFs were
   public on 2026-10-18. All of this predates `eebe9508` (2026-10-19 23:10Z). The doc's
   hedge ("absence of a hit is not proof") was honest, but the verdict block states the
   gap as fact, and the evidence sat at the top of the upstream README. The verifier's
   conclusion holds. Its merge date (10-19) is wrong; the correct date (10-15) only
   strengthens the finding.

2. **The bottom line, "don't scope any BC-250 work today", survives.** It survives
   because hapi-1 is physically disassembled on the bench, not because of any software
   gap. Two fleet records confirm this (ISSUE-LOG row 1236 and the `lumen_state`
   history); I could not probe the host itself. With no board to run on, no software
   finding can change today's recommendation. The RTX 3090 ADOPT verdict is unaffected.

3. **The verifier's "orthogonal" claim is plausible but unproven, and the residual risk
   is specific to this board.** Structurally the verifier is right: #2143 touches no
   backend code, uses only common ggml ops, and dispatches through a generic
   `ggml_backend_t`. FLUX.3 should therefore run on Vulkan the way FLUX.1/2 does. But no
   one has published a FLUX.3 Vulkan run. The nearest pipeline, FLUX.2-klein, has open
   Vulkan bugs on AMD shared-memory hardware (#1718 buffer-size OOM in Qwen3 conditioning,
   #1665 white output on img2img). gfx1013 adds its own unknowns: the fleet's proven Vulkan
   workload on it is llama.cpp LLM inference, which never exercises the conv2d/im2col VAE
   paths a diffusion run needs. So the doc's narrower fallback reading, "unconfirmed on
   Vulkan, not 'it'll just work'", is still true. Only its stated reason, a missing
   architecture, is wrong. What remains unknown is whether gfx1013/RADV runs it, which
   no upstream release can settle.

4. **What to correct in wq#4425** (to be applied separately, per archetype 8):
   - Verdict block L5-7 and §3 L72-74: replace "no confirmed support yet for FLUX 3's new
     tensor layout" with: "upstream added FLUX.3 Klein/Klein Base support 2026-10-15 (PR
     #2143, `8cd731e7`) with official GGUFs (`leejet/flux-3-image-klein[-base]-GGUF`);
     FLUX.3 on the Vulkan backend is not yet demonstrated anywhere, and adjacent FLUX.2-klein
     Vulkan bugs on AMD shared-memory parts (#1718, #1665) are open."
   - §3 L62-64: change "that path is closed on hapi-1 regardless" to say that stock ROCm
     is unsupported and a bleeding-stack ROCm/PyTorch experiment lane exists per
     `425_hapi1_bringup_runbook.md` §5, which the fleet has not reproduced and which is
     not a production path.
   - §5 condition (b): replace "sd.cpp confirms FLUX 3 architecture support" (already met)
     with a check that can actually fail: "an `sd-cli --backend diffusion=vulkan0` FLUX.3
     Klein Q4 smoke run (txt2img, plus one `-r` edit) produces correct output on hapi-1's
     RADV gfx1013." Only (a) blocks this today, and (a) has to clear before (b) can be
     tested at all.
   - §6: attribute PR #291 to `leejet/stable-diffusion.cpp` (sohzm, 2024-08-27). Note that
     `rmatif/…` is a fork.

5. **Follow-up ticket: yes, small and parked.** The software path is now real enough to
   test, so file one ticket blocked on hardware: "BC-250 FLUX.3 Klein sd.cpp Vulkan smoke
   test", dependent on `hapi-1.bringup_eta` going live. Use leejet's GGUFs, not
   `realrebelai`'s, because those keep ComfyUI tensor names and nobody has tested them
   with sd.cpp's loader. Pin `--backend te=cpu` as a first fallback if #1718-style
   conditioning OOMs appear. This is cheap and closes the only real open question. It is
   not urgent, and it should not hold up the 3090 BUILD ticket.

6. **Both passes missed something that affects the 3090 BUILD ticket.** The doc scoped
   that ticket to a net-new ComfyUI + PyTorch install because it believed only ComfyUI
   ran FLUX 3. sd.cpp's CUDA backend now runs the same model as a single native binary.
   That could be a lighter install than ComfyUI on the 3090, and it would put the 3090
   and a future hapi-1 on one runtime. The BUILD ticket should compare sd.cpp CUDA with
   ComfyUI before committing to ComfyUI. This is a scoping input, not a reversal: ComfyUI's
   official workflow templates remain an advantage, and the BFL gated-VAE account task is
   needed on either path.
