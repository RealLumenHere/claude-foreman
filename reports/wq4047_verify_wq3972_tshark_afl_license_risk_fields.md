# wq#4047 — verify wq#3972: LIMPET BUILD: add tshark + afl++ to core tool seed

**Verdict: CONTRADICT** (escalating to opus-5@high per ticket instructions)

PR: lumen/claude-foreman#1986 (merged 4b973906). Author model: claude-sonnet-5.
Verifier: claude-sonnet-5@high (fresh instance), per wq#4047.
Worktree: `/home/lumen/worktrees/wq4047-a1`.

## Scope

wq#3972 adds two new "core"-tier tool manifests —
`limpet/registry/tools.d/tshark.yaml` and `limpet/registry/tools.d/aflplusplus.yaml`
— plus matching entries in `limpet/tool-catalog.json` (42 -> 44 tools,
`catalog_version` 0.2.0 -> 0.2.1). Ticket ACCEPT: "manifests validate, catalog
reconcile (wq#3936 tooling) clean, ISO package list includes both."

## What I checked

1. **Schema validation (ticket's own ACCEPT #1).** Loaded both new manifests
   through `limpet/registry/schema.validate_tool()` directly — no errors — and
   through `registry.load_tool_dir()` against the full `tools.d/` directory — 6
   tools load, 0 errors, both `tshark` and `afl++` present. Confirmed, not
   vacuous.

2. **Existing test suites (ticket's done-notes claim "25+15 tests pass").**
   `python3 -m pytest limpet/registry/tests/test_registry.py` -> 25/25 pass.
   `python3 -m pytest limpet/registry/tests/test_routing.py` -> 12/12 pass.
   `python3 -m pytest limpet/iso-builder/test_gen_tool_packages.py` -> 6/6 pass.
   The 25 matches exactly; I could not reproduce an exact "+15" sibling count
   (12 and 6 are the closest candidates) — a minor done-notes imprecision, not
   a functional defect, noting it but not treating it as a contradiction.

3. **Ran the real (non-mocked) `gen_tool_packages.py` against the live
   catalog and this host's real `dpkg` database**, to check the "ISO package
   list includes both" claim for real rather than only under the unit test's
   injected fake runner. Result: `tshark` *did* resolve to the `tshark` apt
   package (this worktree host happens to have it installed); `afl++` did
   **not** — `dpkg -S /usr/bin/afl-fuzz` reports no owning package on this
   host. However, pre-existing "core" tools with no reference node yet
   (`ghidra`, `radare2`, `unblob`) show the identical "unresolved" outcome on
   this same host, so this is the already-accepted "not yet re-verified on a
   dedicated Limpet node (none exists yet)" posture that predates this ticket,
   not a regression specific to wq#3972. Not treating this as a contradiction.

4. **Citation accuracy.** Checked every design-doc line range the new entries
   cite: `design/wq3929_competitive_analysis.md:171-173` (tshark/afl++ named as
   the defensible `core` additions, gap G6), `:417-418` (G6 narrow-scope note),
   `:493-494` (A10 -> G6 action item), and `TOOL_REGISTRY.md:199,210` (fuzzware
   pipeline naming AFL/AFL++ as its fuzzing backend). All four check out
   verbatim against the cited files — the AFL++-over-honggfuzz reasoning and
   the `trust: core` classification are both faithful to the design doc, which
   itself says "add as `core`." No fabricated or stale citations found here
   (contrast wq#4046's finding on a sibling ticket, which found a stale but
   inherited citation — not the case here).

5. **Tried to break it: cross-checked the new entries against the catalog's
   own, currently-in-force field policy.** `tool-catalog.json`'s
   `provenance.license_risk_policy` (introduced by the *immediately preceding*
   commit touching this file, `2c603e62`, wq#3959) states in full:

   > "Every tool entry going forward MUST also carry 'license_risk' (none |
   > copyleft-source-offer | non-osi-commercial-restricted | unverified) and
   > 'redistribution_posture' (bundle | external-invoke |
   > blocked-pending-permission)."

   `git log --oneline -- limpet/tool-catalog.json` confirms `4b973906`
   (wq#3972) is the very next commit to touch this file after `2c603e62`
   introduced that MUST. Neither new entry (`tshark`, `afl++`) carries
   `license_risk` or `redistribution_posture`. Only one entry in the whole
   44-tool catalog carries these fields at all: `ghidra`, retrofitted by the
   wq#3959 commit itself as the worked example (`grep -c '"license_risk"'
   tool-catalog.json` -> 1).

   This is not a paperwork nit. `tshark`'s `license` field is correctly set to
   `GPL-2.0`, and the entry is `trust: core` (the catalog's own
   `trust_tiers` doc: "core = proven on fleet, in base image" — i.e., bundled
   into the commercially-sold Limpet ISO). The license_risk_policy text gives
   the exact analogous case: a GPL-3.0 tool (EMBA) "safe to bundle with a
   source offer" gets `license_risk=copyleft-source-offer`. GPL-2.0 is the
   same copyleft family and the policy's whole stated purpose is to catch
   precisely this — "the free-text 'license' field alone is not enough to
   catch redistribution risk" for a tool about to be baked into the base
   image. By omitting the two mandatory fields, wq#3972 lets a new copyleft
   tool into the `core`/bundle tier without running it through the gate that
   the immediately preceding ticket built for exactly this purpose. (`afl++`
   is `Apache-2.0`, lower risk, but is equally missing both fields and would
   presumably need `license_risk=none` recorded, not silently absent.)

   No automated check currently enforces this (confirmed:
   `grep -rln license_risk limpet --include='*.py'` -> no hits), so nothing in
   CI or the shipped tests would have caught this — the gate is
   policy/schema-in-the-JSON-comment only, which is exactly how it was missed.

## Verdict rationale

Everything the ticket's ACCEPT criteria literally asked for is genuinely true
and independently re-run (schema validates, existing suites pass, citations
check out). The contradiction is that the diff violates a different, explicit,
already-in-force MUST from the catalog file it edits — a requirement added by
the commit directly before it in the same file's history — for a newly
bundled GPL-2.0 tool, which is the exact scenario that requirement exists to
catch. This is a real, falsifiable, reproducible gap (grep for the fields,
count them: 0 of 2 new entries have them; 1 of 44 total catalog entries has
them), not a style preference.

Per ticket instructions, escalating to opus-5@high for judgment rather than
re-implementing the fix myself.
