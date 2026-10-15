# wq#3986 — verify-pass of wq#3959 (LIMPET license audit: OGhidra/EMBA/FACT/FAT 2.0)

PR #1933, merged `2c603e620f7161984320ec5df4a8fcb0cb352caa` (gitea PR URL not independently
resolvable from this worktree — `http://100.121.17.5:3000/lumen/claude-foreman/pulls/1933`
404s over plain curl, same as other already-merged PR numbers tried for comparison, so this
looks like an auth/reachability property of that endpoint rather than a wrong number; citing
the merge SHA instead of guessing a working link).
Author model: claude-sonnet-5. Verifier: claude-sonnet-5@high (fresh instance).
Verdict: **CONFIRM** — no contradiction, no escalation to opus-5@high.

## Scope read cold

One commit, 3 files, +198/-2: `design/wq3959_license_audit_oghidra_emba_fact_fat.md` (new,
177 lines — the audit), `TOOL_REGISTRY.md` (+16, cross-reference section), and
`limpet/tool-catalog.json` (+7/-2 — a new `provenance.license_risk_policy` convention plus a
worked `license_risk`/`redistribution_posture` example on the existing `"ghidra"` entry).
Claims: EMBA and FACT_core are both GPL-3.0 (safe to bundle w/ source offer); FAT 2.0 is
FSL-1.1-ALv2 with a "Competing Use" clause (external-invoke only); "OGhidra" is a real,
distinct LLM-to-Ghidra bridge project whose license the author could not verify this session
(declared research tooling down), given the same conservative external-invoke-only posture
pending confirmation.

## What I ran

1. **Re-fetched every primary source the author cited**, independently, via
   `/home/lumen/bin/fleet-search read`:
   - EMBA `LICENSE` (raw.githubusercontent.com/e-m-b-a/emba/master/LICENSE): HTTP 200, 35144
     bytes, full GPLv3 text — byte-for-byte match to the audit's citation, and the cache
     timestamp (`2026-10-15T05:08:15Z`) falls inside wq#3959's own claimed/done window
     (05:04:27–05:17:31), confirming this is a real fetch from that session, not fabricated.
   - FACT_core `LICENSE` (same repo pattern): HTTP 200, 35141 bytes, same GPLv3 text.
   - FACT_core `README.md`: badge text `[![License: GPL v3](...)](https://www.gnu.org/licenses/gpl-3.0)`
     — exact match to the audit's quote, confirming the audit's correction of the ticket's
     "GPL-3.0-or-later" claim to plain GPL-3.0.
   - FAT 2.0 repo page (github.com/attify/firmware-analysis-toolkit): contains the exact quoted
     line, *"FAT 2.0 is source available under FSL-1.1-ALv2. See LICENSING.md and
     THIRD_PARTY_NOTICES.md for more details."*
   - `fleet-search web "OGhidra license"` right now: still fails exactly as the author
     described (brave/google cse rate-limited, duckduckgo/startpage CAPTCHA, no
     `BRAVE_API_KEY` fallback, wq#2595) — the stated research-tooling outage is real, not an
     excuse.
   All reproduced cleanly. No discrepancy found in anything the author actually verified.

2. **Adversarial probe outside the author's own verified population** (archetype-8 "try to
   break it," not just rerun what they ran): the author explicitly left OGhidra as an open
   item ("could not be independently verified this session... pending confirmation" —
   `design/wq3959_license_audit_oghidra_emba_fact_fat.md:120-159`). I had `curl` and raw
   `api.github.com` access in this session (the author's fetch path apparently did not resolve
   repo-attribution metadata from GitHub's search UI/API response, per their own note about
   "result pages strip repo-attribution metadata through this session's text-extraction fetch
   path"). Using the plain, unauthenticated GitHub REST search API directly:
   - `curl -s "https://api.github.com/search/repositories?q=oghidra"` → 3 hits, top one
     `llnl/OGhidra`, license field `{"key":"other","spdx_id":"NOASSERTION"}` (GitHub can't
     classify it as a standard license — consistent with a non-standard template).
   - `curl -s "https://api.github.com/repos/llnl/OGhidra"` → description *"OGhidra bridges
     Large Language Models (LLMs) via Ollama with the Ghidra reverse engineering platform,
     enabling AI-driven binary analysis..."* — exact match to the grep.app snippet the author
     already found, confirming it's the same project.
   - `curl -s "https://raw.githubusercontent.com/llnl/OGhidra/main/LICENSE"` → full text:
     **"BSD 3-Clause with Commercial License alternative"**, Lawrence Livermore National
     Security LLC, clause 4 ("Commercial Use... requires a separate, written commercial
     license from the copyright holder... contact LLNS at softwarelicensing@llnl.gov").
   This is a byte-for-byte match to the *ticket's own original claim* ("restricts commercial
   use without separate written permission (not plain BSD-3-Clause despite appearances)") and
   to the posture the audit already applied (`license_risk=non-osi-commercial-restricted`,
   `redistribution_posture=external-invoke`). **The applied posture is correct** — this probe
   could have falsified it (OGhidra could have turned out permissively licensed, which would
   have made the audit over-conservative) and didn't.
   - Also pulled FAT 2.0's `LICENSING.md` directly via `curl` (the author reported this file
     "fetched but not text-extractable this session"). Full text confirms the audit's
     "Competing Use" paraphrase and goes further: it explicitly lists *"Selling a product...
     that includes FAT"* as *"Needs permission"* — direct, stronger support for the audit's
     bundle=high-risk conclusion than what the audit itself had in hand. `api.github.com/repos/
     attify/firmware-analysis-toolkit/releases` shows v2.0.0 published 2026-09-25 (three weeks
     before today), confirming the audit's claim that the FSL two-year Apache-2.0 conversion
     window "has almost certainly not passed."

3. **Schema/regression check on the `tool-catalog.json` edit**: the new
   `license_risk`/`redistribution_posture` fields are catalog-only bookkeeping, not part of
   the strict manifest schema (`limpet/registry/schema.py` `REQUIRED_FIELDS`/
   `OPTIONAL_DEFAULTS` reject unknown fields, and pre-existing catalog fields like `url`,
   `license`, `trust`, `provenance` already aren't in that allowlist either —
   `import_catalog.py`'s `catalog_to_manifest()` maps only a fixed alias list into the
   manifest, so these new fields never reach schema validation). Confirmed only 1 of 42
   catalog entries (the `"ghidra"` worked example) carries the new fields — correct, since the
   policy text is forward-looking ("every future tool entry"), not a retroactive backfill
   claim. `python3 -I -m pytest limpet/registry/tests/test_registry.py -q` → 25/25 pass,
   `test_import_catalog_from_fixture` included. Valid JSON (`json.load` succeeds, all
   top-level keys intact).

## Minor note (not a contradiction)

FAT 2.0's `LICENSING.md` also carries a component-level exception the audit doesn't mention:
`scripts/fat-hook-plugin.c` is separately GPL-2.0-or-later with its own distribution caveat
("FAT releases must not include a compiled hook or QEMU binary without a separate
distribution/compliance review"). The audit's external-invoke-only posture already covers this
(Limpet ships none of FAT's code either way), so it doesn't change the conclusion — flagging
only because a future bundling decision for FAT should re-read `LICENSING.md` component
exceptions directly rather than relying solely on this audit's top-level summary.

## Conclusion

Every claim the author actually verified reproduces byte-for-byte against the same primary
sources. The one item the author left open (OGhidra) is independently resolvable with the
unauthenticated GitHub REST API plus `curl` — a path that doesn't appear to have been fully
available or productive via the author's text-extraction fetch tooling that session — and
confirms, rather than overturns, the posture already applied. The `tool-catalog.json` schema
change is additive, catalog-only, and doesn't touch the strict manifest validator; registry
tests pass 25/25. **CONFIRM.** Recommend a cheap follow-up ticket to fold the now-available
OGhidra primary-source citation (`llnl/OGhidra`, BSD-3-Clause-with-commercial-license-rider)
into `design/wq3959_license_audit_oghidra_emba_fact_fat.md`'s open item — not required to
accept this PR, since the conservative posture it already applies is the same one full
verification yields.
