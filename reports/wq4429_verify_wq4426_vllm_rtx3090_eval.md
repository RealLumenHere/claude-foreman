# wq#4429: verify-pass on wq#4426's vLLM-on-RTX-3090 research doc (PR#2313) — CONFIRM

Author: claude-sonnet-5. Verifier: claude-sonnet-5@high (fresh instance, no
prior context on wq#4426 beyond the ticket text and the PR diff).

## What wq#4426 shipped (PR#2313, merged `0f397aaf`)

One new file, `research/VLLM_RTX3090_EVAL.md` (116 lines, no code). Verdict:
recommend vLLM over Ollama for the single-model high-concurrency serving
role once 2x RTX 3090 land; keep Ollama for the fleet's current
swap-heavy many-small-models role. No GPU exists in the fleet today, so the
doc is explicitly a desk evaluation, not a benchmark, with a 4-step A/B test
plan queued for when the hardware arrives (queued as physical task wq#4428).

## Independent re-derivation (cold, not trusting the diff)

**Fleet-state premise ("zero discrete GPUs, nothing to benchmark today"):**
- `grep -i "3090\|gpu\|vram\|cuda" research/LOCAL_MODEL_STRATEGY.md` → one
  hit, `anubis (OptiPlex 3090)`, the chassis model, not a GPU. Matches claim.
- `grep -i "3090" research/INVENTORY_RECONCILIATION.md` → lines 83-84, 343,
  all the same OptiPlex 3090 UFF chassis (host `anubis`, i5-1145G7 iGPU-only
  CPU). Matches claim.
- Broadened the sweep past the doc's own grep: `grep -rn -i
  "nvidia\|rtx \|geforce\|radeon\|cuda" research/INVENTORY_RECONCILIATION.md`
  turns up one hit the doc didn't mention — an AMD Radeon E9170 "Lexa PRO"
  2GB embedded GPU, flagged in the inventory itself as "VIDEO OUTPUT ONLY
  (Frank directive)". This is a discrete GPU the doc's "no discrete GPU"
  line technically overlooks, but it's AMD (wrong vendor for a vLLM/CUDA
  question), 2GB (below even vLLM's smallest-model floor), and explicitly
  excluded from compute by standing directive — irrelevant to the verdict.
  Noting it, not treating it as a contradiction.

**OLLAMA_NUM_PARALLEL "fleet default of 1" claim** (§2 last bullet, cited as
`docs/hardware-tiers-spec.md:98,123`, used again in §4 step 1): read all
five tiers directly —
- Line 98 is Tier 3 (CPU, `RECOMMENDED`); line 123 is Tier 4 (CPU,
  `OPTIMAL`). Tiers 1 and 2 don't set `OLLAMA_NUM_PARALLEL` at all. **Tier
  5 — ENTHUSIAST (discrete GPU), the only tier actually relevant to an
  RTX-3090 question — has no `OLLAMA_NUM_PARALLEL` setting anywhere in its
  section (`docs/hardware-tiers-spec.md:140-174`)**, only
  `OLLAMA_GPU_LAYERS=auto`.
- So the doc's "every tier" framing and its two line citations point at two
  CPU tiers, not the GPU tier the recommendation is about — a real citation
  inaccuracy.
- However, the substantive claim survives independently: `fleet-search web
  "OLLAMA_NUM_PARALLEL default value 2026"` returned
  `morphllm.com/comparisons/vllm-vs-ollama`, which states Ollama's own
  upstream default (when unset) is 1. Since Tier 5 doesn't override it, the
  fleet's GPU tier does inherit `NUM_PARALLEL=1` in practice — just not for
  the reason the doc cited. §4 step 1's A/B plan ("baseline at
  `OLLAMA_NUM_PARALLEL=2`, not the fleet default of 1") is still correct in
  substance. Downgraded from contradiction to citation-precision nit.

**External citations spot-checked** (doctrine: live-search first, verify
what's checkable):
- `CVE-2026-100648` — real. `fleet-search web "CVE-2026-100648 vLLM audio
  decode"` → `vulnerability.circl.lu/vuln/cve-2026-100648`: vllm-project/vllm
  "Uncontrolled Resource Consumption via Audio Decoding", affected `<0.29.0`,
  CWE-400. Matches the doc's description exactly (audio-decode DoS, fixed in
  0.29.0).
- vLLM GPU compute-capability floor (Volta CC7.0 minimum, Ampere CC8.0
  recommended) — real and matches the exact source cited.
  `fleet-search web "vLLM minimum compute capability Volta Ampere supported
  GPUs"` returned the same `learn.microsoft.com/.../foundry-local/
  reference-models` page the doc cites, with the same CC7.0-min/CC8.0-rec
  framing. RTX 3090 is Ampere SM 8.6, clears both floors — correct.
- `github.com/ollama/ollama/issues/17666` (cited for "Ollama's open
  serialization issue is about MLX, not CUDA") — **could not independently
  confirm**. No `gh`/GitHub API access from this worktree (401, no usable
  token) and WebFetch is not granted to this worker; SearXNG's general
  engines were down fleet-wide during this pass (brave/duckduckgo/google
  cse/startpage all suspended or CAPTCHA'd), leaving only the DeepSeek
  fallback, which surfaced related-but-not-matching issues (e.g. #15017, a
  different parallel-request bug) and never the exact #17666 thread. This is
  a verification gap, not a finding of fabrication — per instinct 8, an
  inability to fetch is not evidence the claim is false, only that it's
  unverified by this pass. The point it supports (Ollama's CUDA/llama.cpp
  path isn't newly fixed for concurrency) is also not load-bearing for the
  verdict — the doc's own §4 already proposes testing `OLLAMA_NUM_PARALLEL=2`
  empirically rather than resting on this citation alone.
- Blog-sourced throughput deltas (runpod.io, openclawdc.com, xhinker.medium.com,
  llmconfigurator.com) are all explicitly hedged by the doc itself as
  single-source, non-fleet-general numbers ("not a fleet-general constant",
  "plausibility bound, not a spec number") — appropriate epistemic hygiene,
  not re-verified individually since the doc doesn't rest its verdict on
  their precision.

**Physical-task gating** (instinct 3 compliance): `wq show 4428` confirms a
real ticket exists — "Buy/rack 2x RTX 3090 ... for vLLM inference eval",
`status: frank_gated`, `gate_type: frank_decision`, carrying a `[NO-PROBE:
physical hardware purchase]` tag and referencing the same §4 A/B plan. The
GPU-purchase dependency was correctly queued as a physical task, not left as
a blocking question or silently assumed.

## Probe

This is a research-only ticket (`wq show 4426`: archetype
`standard-engineering`, no script/code shipped, one markdown file). There is
no acceptance probe to run — the "probe" is the §4 test plan itself, which is
explicitly `[UNRESOLVED]` pending hardware that doesn't exist yet. Confirmed
this is the correct call, not a dodge: the doc states the no-GPU premise
up front and backs it with the grep above, rather than fabricating a
benchmark number it couldn't run (instinct 8 compliant).

## Verdict

**CONFIRM.** The fleet-state premise (no RTX 3090, no discrete compute GPU)
holds under a broader sweep than the doc itself ran. Both externally-checkable
technical citations (CVE-2026-100648, vLLM's CC7.0/CC8.0 floor) are real and
accurately represented. One citation is imprecise — `docs/hardware-tiers-
spec.md:98,123` are Tier 3/4 (CPU), not Tier 5 (GPU), and Tier 5 sets no
`OLLAMA_NUM_PARALLEL` at all — but the substantive claim it supports (Ollama's
effective default is 1) is independently confirmed via Ollama's own upstream
default, so the verdict and the §4 test plan are unaffected. One citation
(Ollama GitHub issue #17666) could not be independently verified with the
tools and network access available to this worker; flagged as an open gap,
not a contradiction, since nothing in the verdict depends on it alone. No
contradiction found; nothing to escalate to judgment.
