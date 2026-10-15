# wq#3952 — judgment: verify-pass of wq#3939 (AMD tier definitions)

**Ticket under verify:** wq#3939, "LIMPET BUILD: complete AMD tier definitions in
hardware-tiers-spec.md" — PR #1904, merged `24291c56e2` into `/home/lumen/foreman`.
**Archetype:** 8 (adversarial verify-pass). **Author model:** claude-sonnet-5.
**Verifier:** claude-sonnet-5@high (this ticket, wq#3952), fresh instance.
**Escalated to:** opus-5@high, on a confirmed contradiction (self-grading risk —
author and verifier are the same model).

## What was checked

- Read the full diff (`git show 24291c56e2`): new `docs/hardware-tiers-spec.md`
  (918 lines) + 5 standalone TOML files under `limpet/hardware-book/amd/`.
- Validated all 5 TOML profiles parse with stdlib `tomllib` — pass (acceptance
  probe: malformed TOML or a parse exception would have failed this; it did not).
- Executed the §3.9 `detect_amd_topology()` Python snippet against this host's real
  `/sys/devices/system/cpu` tree — ran without error, returned a sane single-CCD
  result (this host has `ccd_count=1`, `needs_pinning_bench=False`); exercises the
  `die_id`/L3-fallback/`lscpu -p=NODE` code paths for real.
- Confirmed all 8 of wq#3939's enumerated acceptance items are present: Zen 1-5
  table (§3.1), Threadripper 4A tier + NUMA/Ollama tuning (§3.3/§3.4/§3.9/§3.10),
  EPYC 4E tier + multi-socket notes (§3.3/§3.4), APU ROCm section (§3.2.6), consumer
  Radeon ROCm matrix (§3.2.7 table), 5 named TOML profiles (§3.4), AMD-side topology
  detection replacing the P/E split (§3.9), advanced-settings exposure (§3.10).
- Checked ISSUE-LOG row 1328 (the non-git-artifact issue wq#3939 logged) against the
  actual non-git draft at `/home/lumen/projects/limpet/docs/hardware-tiers-spec.md`
  — the draft exists, is a real pre-existing v0.1 document, and the row's account of
  what happened matches it. Not fabricated.
- Diffed the ported sections 1-2 (Intel tiers, unrelated to wq#3939's AMD scope)
  against the pre-wq3939 draft line-for-line, and diffed §5's `detect_tier()`
  reference algorithm the same way, to find anything wq#3939 touched outside its
  stated AMD scope.
- Spot-checked whether `docs/settings-spec.md:150-168`'s self-citation (in the new
  §3.10) was accurate: it was, **at the time wq#3939 was committed** (03:05:24) —
  settings-spec.md's §4.2 started at line 150 then (from the wq#3904 draft,
  committed 2026-10-14 21:16:44). A later same-day ticket, wq#3938 (committed
  03:47:15, confirmed via `git merge-base --is-ancestor`), extended
  settings-spec.md and pushed §4.2 down to line 188, which is why the citation
  reads wrong *today*. This is citation rot from a later ticket, not a wq#3939
  defect — not reported as a finding.
- Attempted to independently re-verify a sample of the cited third-party benchmark
  numbers (e.g. EPYC 9654 DIMM-population tg, llamafile discussion #450) via
  `fleet-search web` and `fleet-search github`. Both failed on pre-existing fleet
  infra problems (all general web-search engines suspended/CAPTCHA'd per wq#2595;
  GitHub search unauthenticated per ISSUE-LOG row 325/715). Could not independently
  re-verify primary sources this session; not counted against wq#3939 since the doc
  already labels every number's evidence tier ([M]/[U]/[D]/[V]) and the blocking
  infra issue is already logged.

## Finding 1 — CONFIRMED CONTRADICTION (primary, escalation trigger)

**§5's `detect_tier()` reference algorithm, which wq#3939 itself edited to add AMD
support, misclassifies Zen 1 — contradicting §3.1's own tier table in the same PR.**

- §3.1 (`docs/hardware-tiers-spec.md:206`) explicitly tiers AMD Zen 1/Zen+ at
  **Tier 2**.
- §5 (`:761-793`) only special-cases AMD at Step 3b (`:780`), gated on
  `cpu_info.get('avx512')` — true only for Zen 4/5. A Zen 1 part (no AVX-512) falls
  through to the generic bandwidth check (Step 4, `:789`: `<30 GB/s → Tier 2`) and
  then the Step 5 default (`:793`: `Tier 3`).
- Dual-channel DDR4 bandwidth at every real Zen 1 speed grade is well above the
  30 GB/s cutoff (DDR4-2133 ≈ 34.1 GB/s, up to DDR4-2933 ≈ 46.9 GB/s), so the `<30`
  branch never fires for a real two-channel Zen 1 box — the function returns
  **Tier 3**, contradicting the table 3 lines above it.
- Confirmed as wq#3939's own regression by diffing against the pre-wq3939 draft:
  the draft's Step 3 (`:619` in the draft) had no vendor check at all and no AMD
  branch; wq#3939 added the `vendor == 'intel'` guard and the whole Step 3b block.
  The Zen-1 gap is new in this PR, not inherited.

**Escalated to opus-5@high** (full exchange in-session). Opus independently read
the file, re-derived the bandwidth arithmetic (reproduced here and confirmed
correct), and traced the control flow itself rather than trusting the verifier's
paraphrase. **Verdict: contradiction CONFIRMED.**

Opus's additional context, which does not clear the ticket but scopes the fix:
- The *same* 30 GB/s cutoff already misclassifies Intel Tier-2 Skylake/Kaby Lake on
  DDR4-2133/2400 (34-38 GB/s) — but that flaw predates wq#3939 (present in the
  original wq#3806 draft) and is out of scope for this ticket; open separately.
- §3.1's own Zen-1 row is internally in tension ("tg should track the bandwidth
  rule" points toward Tier 3) while still assigning Tier 2 — the table's intent (an
  ISA-penalty floor, not a bandwidth-derived tier) needs to be made explicit in
  whatever fix lands, not just patched in the code.
- Despite this being docs-only pseudocode with every Zen-1 number labeled [U]:
  §8 (`:912`) states the install wizard calls this exact tier-detection algorithm,
  so it is the thing a future implementation will be built from, and the spec's own
  "Conservative" design principle (`:26`: "An underestimated tier still works; an
  overestimated tier causes OOM kills") is exactly what this bug violates — Tier 3
  over-promises a Zen 1 box relative to the tier the research-backed table assigns
  it. Severity: moderate (nothing ships yet, but the control-flow / table
  disagreement is real and self-inflicted by this PR).

**Recommendation (opus, adopted):** reopen wq#3939 for a minimal, scoped fix — add
a Zen-1-specific branch to `detect_tier()` ahead of the generic bandwidth step
(family/model cutoff must be checked against AMD's actual CPUID family-17h model
ranges, not guessed from memory, per the fleet's own "never guess a value, verify
it" doctrine). Do not re-architect the function or touch the out-of-scope Intel
Skylake/Kaby Lake flaw in the same pass.

## Finding 2 — confirmed, lower severity (scope creep / unverified edit)

The same PR silently changed the Intel Tier 1 "Notes" line from "all 104 tools"
(pre-wq3939 draft, `/home/lumen/projects/limpet/docs/hardware-tiers-spec.md:46`) to
"all 129 tools" (`docs/hardware-tiers-spec.md:54`) — a fact entirely outside the
AMD scope wq#3939 was chartered to touch, with no citation and no evidence label,
in a document whose own stated discipline is "nothing unlabeled should be trusted
as a number" (`:180`). A grep of `/home/lumen/foreman` and `/home/lumen/kb` found no
source for either figure. Opus independently confirmed this by reading both lines.
Recommendation: cite a source for 129 (e.g., a live count from the MCP meta-server)
or restore 104 and label it `[U]`, bundled into the same rework pass as Finding 1.

## Verdict

**CONTRADICTED — wq#3939 should be reopened for a minimal, scoped fix.** The
deliverable substantively satisfies all 8 of its acceptance items and the shipped
artifacts (5 TOML profiles, the §3.9 topology-detection code) are structurally sound
and pass their acceptance probes. The contradiction is narrow and the fix is small,
but it is real, self-inflicted by this PR, and sits in a reference algorithm the
spec itself says a future implementation will be built from.

Recorded via `wq review 3939 reject --reason "..."`. ISSUE-LOG row 1336 logs both
findings and this judgment's recommendation.
