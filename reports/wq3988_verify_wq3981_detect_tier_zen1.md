# wq#3988 — verify-pass of wq#3981 (fix detect_tier() Zen 1/Zen+ misclassification vs §3.1)

PR #1938 (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1938), merged as
`98d48b0f` on `main` (branch-tip equivalent `7e53fad6`), one commit, 1 file
(`docs/hardware-tiers-spec.md`, +14/-1). Author model: claude-sonnet-5.
Verifier: claude-sonnet-5@high (this ticket, fresh instance).
Verdict: **CONFIRM** — no contradiction, no escalation to opus-5@high.

## What wq#3981 was fixing

wq#3952's judgment (`reports/wq3952_judgment_wq3939_amd_tier_detect_zen1.md`,
escalated to opus-5@high, contradiction confirmed) found that `detect_tier()`
(§5) misclassified AMD Zen 1/Zen+ as Tier 3, contradicting §3.1's own table
three lines above it in the same prior PR (wq#3939). Finding 2 of that same
judgment flagged an unrelated, uncited, out-of-scope edit in the same PR: the
Tier-1 "Notes" line's tool count silently changed from "104" to "129" with no
citation. wq#3981's liveness probe (from the ticket body): *"detect_tier()
returns 3 (not 2) for an AMD Zen-1 CPU on any dual-channel DDR4 config —
SATISFIED once it returns 2."*

## What I checked

1. **Read the diff cold** (`git show 98d48b0f` / `git show 7e53fad6` — identical
   tree, `8d6193fe`). Single file, two changes:
   - New Step 3c in `detect_tier()` (`:787-798`): if AMD family `0x17` and
     `model < 0x30`, return Tier 2 unconditionally ("regardless of bandwidth"),
     placed **before** the generic Step 4 bandwidth check, as opus's
     recommendation required.
   - Tier-1 notes line reverted from "all 129 tools" to "all 104 tools **[U]**"
     (`:54`), restoring the pre-wq3939 figure and labeling it per the doc's own
     `[U]` = "unvalidated, no measurement/primary source" convention (`:188`).
   No other lines touched — confirms the ticket's explicit "do NOT touch the
   separately-identified, pre-existing, out-of-scope Intel Skylake/Kaby Lake
   30 GB/s cutoff flaw" constraint was honored (Step 4's bandwidth formula is
   byte-for-byte unchanged).

2. **Ran the acceptance probe**, not just read it. §5 is pseudocode inside a
   markdown fence with no standalone module, so I extracted the exact
   `detect_tier()` block from `docs/hardware-tiers-spec.md` (via `python3 -I`,
   no third-party deps) and executed it with stubbed `get_cpu_info()` /
   `get_memory_info()` / `get_gpu_info()` / `detect_amd_topology()` for eight
   scenarios, run from outside the population the original bug was found in
   (not just the one Zen-1 case the probe names):
   - All 5 documented family-17h Zen 1/Zen+ models (`0x01` Summit Ridge, `0x08`
     Pinnacle Ridge, `0x11` Raven Ridge, `0x18` Picasso, `0x20` Dali) at
     dual-channel DDR4 2400–2933 MT/s → **all returned Tier 2.** The liveness
     probe is satisfied, not just for the one case it names.
   - Boundary falsifiability check: family `0x17` model `0x31` (Rome, first
     Zen 2) and model `0x71` (Matisse) at fast DDR4-3600 → **both fell through
     to Tier 3** via the untouched Step 4/5 path, not forced to Tier 2. This is
     the probe that could have failed: a sloppy `<=0x30` or an inverted
     comparison would have misclassified Zen 2 as Zen 1/Zen+ and this would
     have caught it. It did not fail.
   - Intel non-hybrid, slow DDR4-2133 (34 GB/s) → returned Tier 3, not Tier 2.
     This is the **pre-existing, explicitly out-of-scope** Intel
     Skylake/Kaby Lake bandwidth-cutoff flaw opus's note already named
     (30 GB/s cutoff is itself too low for real DDR4-2133 bandwidth) — present
     before this PR and still present after it, confirming the fix did not
     silently "fix" (or further break) the out-of-scope flaw either way.

3. **Independently attempted to re-verify the AMD family/model facts** rather
   than trust the `[D]` label at face value. Ran
   `fleet-search web "AMD family 17h model Zen+ Zen2 Pinnacle Ridge Picasso
   Rome model number cpuid"` — still fails exactly as ISSUE-LOG row 1343
   describes (`brave`/`google cse` rate-limited, `duckduckgo`/`startpage`
   CAPTCHA'd, no `BRAVE_API_KEY` fallback, wq#2595). Confirms the author's
   "search was down" claim is a reproduced fact, not a fabricated excuse
   (instinct 8). Cross-checked the claimed model numbers against my own
   trained knowledge of AMD's public family-17h model table (Linux kernel
   `amd.c`-style model lists): Zen 1/Zen+ parts cluster at `0x01/0x08/0x11/
   0x18/0x20`, Zen 2 parts start at `0x31` (Rome) and stay `>=0x30` through
   Renoir/Lucienne/Matisse/Van Gogh/Mendocino. This matches what the comment
   claims. The exact codename-to-model attribution in the comment (e.g.
   whether Dali is specifically `0x18` or `0x20`) carries minor residual
   uncertainty given the live-search outage, but this is decorative — the only
   thing the shipped logic depends on is the `0x30` boundary itself, which is
   well clear of every known Zen 1/Zen+ model on one side and every known
   Zen 2+ model on the other, as the probe above demonstrates directly.

4. **Confirmed ISSUE-LOG row 1343** exists and accurately states
   root-cause/solution/status — not fabricated.

5. **Checked for scope creep beyond the ticket's two bundled items** — diffstat
   is exactly 1 file, +14/-1, matching only the Step 3c branch and the tools-
   count revert. Nothing else moved.

## Attempted to break it

- Tried an inverted/off-by-one boundary (`<=0x30` instead of `<0x30`) mentally
  against the real Rome model `0x31` — no real family-17h model sits exactly
  at `0x30`, so the strict `<` vs `<=` choice is moot for every known real
  part; not a live bug, just confirmed inert.
- Checked whether the new branch could ever be unreachable or dead code: Step
  3b (AMD + avx512, gating Zen 4/5) uses a disjoint family range (`0x19`+ for
  Zen 3/4, not `0x17`), so there's no overlap that would shadow Step 3c for a
  real chip.
- Checked the defensive default `cpu_info.get('model', 0xFF)`: a missing/
  undetected model key defaults to `0xFF`, which is `>=0x30` and so does
  *not* match — a model-detection failure fails safe (falls through to the
  generic path) rather than wrongly forcing Tier 2.
- Re-read §3.1's Zen 1 row (`:206`) against the new code's returned tier and
  reason string — both say Tier 2, and the new reason string ("half-width
  AVX2 decode; §3.1 Tier 2 floor") now makes explicit the ISA-penalty-floor
  intent opus flagged as implicit in the original table. No remaining tension
  between the table and the algorithm.

## Conclusion

Both items wq#3981 was chartered to fix are fixed, nothing else changed. The
probe the ticket names passes, and the adversarial extensions of it (Zen 2
boundary cases, Intel-path non-regression, dead-branch check, defensive-default
check) all come out clean. The one soft spot — the comment's specific
codename-to-model attributions can't be pinned against a live primary source
because search is still down fleet-wide (reproduced, not assumed) — doesn't
threaten correctness, since the probe exercises the actual boundary value
directly rather than relying on the comment's prose. **CONFIRM.**
