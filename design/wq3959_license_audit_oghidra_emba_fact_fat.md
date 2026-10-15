# wq#3959 — License audit: OGhidra / EMBA / FACT / FAT (Attify) 2.0 vs MIT redistribution risk

## Source gap, stated up front

The ticket cites `docs/research-digest-2026-10-15-thor-docs.md` section A as the source of all
four license claims below. That file does not exist in this worktree or in any of the granted
`--add-dir` roots (`kb`, `foreman`, `bin`, `sentinel`, `mailbot`, `pokemon`, `agents/work-queue`) —
same gap `TOOL_REGISTRY.md` already logged for `re-tools-analysis.md` on the Fuzzware/Sootty
entries (wq#3875/#3878). This audit does not restate the missing digest; it re-derives each
tool's license from the tool's own upstream repository and primary license file, fetched this
session, and cites exactly what was fetched. Where a claim could not be independently confirmed
(OGhidra — see below), that is stated plainly rather than carried forward as fact.

**Research-access note**: `fleet-search web` (SearXNG) was fully down for this session — all
four general engines (brave, duckduckgo, google cse, startpage) refused with
rate-limit/CAPTCHA, matching the already-logged outage (`kb/ISSUE-LOG.md` rows 715/639 area,
wq#2595/#2599: missing `BRAVE_API_KEY` fallback). `fleet-search github` is unauthenticated
(`kb/ISSUE-LOG.md` row 325, wq#1717, OPEN). Direct `fleet-search read <url>` against raw GitHub
content worked throughout and is the primary evidence source for EMBA/FACT/FAT below.

## Redistribution-posture vocabulary used below

- **external-invoke**: Limpet shells out to a copy the operator installs/updates themselves
  (subprocess call, no code from the tool ships inside the Limpet ISO/repo). No license's
  copyleft or source-offer terms are triggered by this, because Limpet distributes none of the
  tool's code. This is the universally-safe posture regardless of license.
- **bundle**: Limpet's ISO build (`limpet/iso-builder/`) packages the tool's own unmodified
  binary/package inside the appliance image it ships/sells. Triggers each license's
  redistribution terms.
- **vendor/relicense**: forking or embedding (modified) source into Limpet's own tree. Triggers
  the strictest terms of whichever license applies, plus copyleft "virality" if the embedding
  tool is GPL and the result is distributed.

## 1. EMBA — `e-m-b-a/emba`

**License: GPL-3.0.** Confirmed by direct fetch of
`https://raw.githubusercontent.com/e-m-b-a/emba/master/LICENSE` (HTTP 200, 35144 bytes, full GNU
GPL v3 text, "Version 3, 29 June 2007").

GPL-3.0 carries **no commercial-use restriction at all** — the license text itself states
distribution "whether gratis or for a fee" is explicitly permitted. Its obligation is a
source-availability requirement that attaches only to **distributing** EMBA (as-is or modified):
anyone who receives a copy must also be able to get the corresponding source.

- **external-invoke** (agent shells out to an EMBA the operator installed): no obligation
  triggered — Limpet distributes none of EMBA's code. Lowest-risk posture, matches wq#3929's
  "run the automated firmware pass and ingest its findings" framing
  (`design/wq3929_competitive_analysis.md:185-186`).
- **bundle** (ship EMBA's package inside the Limpet ISO, unmodified): permitted, conditioned on
  Limpet also offering EMBA's corresponding source (upstream repo URL + pinned commit is
  sufficient; no obligation to vendor a source tarball ourselves as long as the offer is real
  and durable). Risk: **low**, administrative only.
- **vendor/relicense**: only relevant if Limpet patches EMBA and ships the patched result —
  triggers copyleft; the patched EMBA source would also need to be GPL-3.0 and offered. Does not
  affect Limpet's own, separate codebase (GPL's copyleft does not extend to software that merely
  runs alongside/invokes it over a process boundary).

## 2. FACT — `fkie-cad/FACT_core`

**License: GPL-3.0** (the ticket's digest claims "GPL-3.0-or-later" — not confirmed; corrected
below). Confirmed by:
- `https://raw.githubusercontent.com/fkie-cad/FACT_core/master/LICENSE` (HTTP 200, 35141 bytes,
  the same GNU GPL v3 text as EMBA's).
- `https://raw.githubusercontent.com/fkie-cad/FACT_core/master/README.md` badges
  `[![License: GPL v3](...)](https://www.gnu.org/licenses/gpl-3.0)` — plain v3, not "or later".
  The ticket's digest is imprecise on this point; it does not change the redistribution analysis
  (GPL-3.0 and GPL-3.0-or-later have identical obligations for a distributor who isn't exercising
  the "or later" option).

Same analysis as EMBA: no commercial-use restriction; source-offer obligation only on
distribution. **external-invoke**: no obligation, lowest risk. **bundle**: low risk,
administrative (source-offer) only.

## 3. FAT (Attify Firmware Analysis Toolkit) 2.0 — `attify/firmware-analysis-toolkit`

**License: FSL-1.1-ALv2** (Functional Source License 1.1, Apache-2.0 future license).
Confirmed verbatim from the repo's own README via
`https://github.com/attify/firmware-analysis-toolkit` (fetched this session): *"FAT 2.0 is
source available under FSL-1.1-ALv2. See LICENSING.md and THIRD_PARTY_NOTICES.md for more
details."* This matches the ticket's claim exactly and is the one claim in the digest that is
fully corroborated.

FSL-1.1 is **not OSI-approved open source**. Its terms (standard FSL template, per
functionalsourcelicense.com, which this specific repo's shortened, non-boilerplate
`LICENSE`/`LICENSING.md` — 3.7KB, fetched but not text-extractable this session — is consistent
in length with using unmodified):
- Permits use, modification, and self-hosting for *any purpose* **except "Competing Use"** —
  offering the software (or a derivative) as part of a product or service that competes with the
  licensor's own commercial offering.
- Each released version automatically relicenses to the stated "future license"
  (here, Apache-2.0) two years after **that version's own** release date — not a blanket
  calendar date. "2.0" reads as a 2026-era release, so this change date has almost certainly not
  passed yet for the current version.

**Why this is the real risk in the ticket**: Limpet/Lumen ships a commercially-sold appliance
(`design/wq3929_competitive_analysis.md` benchmarks it against commercial/competing products;
hardware-tiers-spec.md, branding-spec.md, and the wq#2714 D300 GPU resale SOP all point at an
actual sale channel). Attify's own business is firmware/IoT security tooling and training —
directly adjacent to, arguably competing with, a commercially-sold firmware-RE appliance. That is
exactly the scenario FSL's "Competing Use" clause exists to block without a paid/negotiated
license from Attify.

- **external-invoke** (the *operator* installs FAT 2.0 themselves; Limpet's agent shells out to
  it if present, Lumen never packages or resells it): does not trigger the restriction — Lumen
  distributes nothing. **This is the only safe posture without a commercial license from
  Attify.**
- **bundle** (ship FAT 2.0 inside the sold Limpet ISO): **high risk** — plausibly a prohibited
  Competing Use under FSL-1.1 as currently licensed. Do not bundle without either (a) a written
  commercial license from Attify, or (b) confirming the specific bundled version's two-year
  change date has already passed (unlikely for a "2.0" release in 2026).
- **vendor/relicense**: blocked outright; FSL explicitly disallows this for a competing product
  regardless of modification.

**Recommendation for wq#3929's P0 integration plan**: scope FAT 2.0 as external-invoke only
(agent detects/shells out to an operator-installed copy), not an ISO-bundled package, until
someone gets a commercial read from Attify. This is a plan-scoping note, not a Frank-gate — no
physical action is required to apply it, so it is applied here as the working assumption rather
than parked.

## 4. OGhidra — license claim NOT independently verified

**Status: real project, license unconfirmed.** This is not the fabricated-value case (Tier-0
instinct 8) — OGhidra is independently corroborated as a real, existing tool, distinct from the
NSA's Ghidra already in `limpet/tool-catalog.json` (`"ghidra"`, Apache-2.0, verified
`TOOL_REGISTRY.md:15` 2026-10-14):

- A GitHub repository search for `oghidra` (fetched via
  `https://github.com/search?q=oghidra&type=repositories`, this session) returns a real hit
  described as: *"OGhidra bridges Large Language Models (LLMs) via Ollama with the Ghidra reverse
  engineering platform, enabling AI-driven binary analysis…"* — an LLM-to-Ghidra bridge, not a
  Ghidra fork.
- A `grep.app` code search for `OGhidra` (fetched this session) surfaces it vendored/integrated
  into at least one other project (`run_oghidra_analyze`, `run_oghidra_conversational`,
  `OGhidraExecutor`, `parse_oghidra_output` — a "T3MP3ST + OGhidra Enhancements (v3.6)" changelog
  entry).

What could **not** be pinned down this session, despite trying GitHub's own search UI, the
GitHub API (unauthenticated, wq#1717), `grep.app`'s search API, Bing, DuckDuckGo-lite, and
Sourcegraph: the canonical repo owner/URL, and therefore the actual `LICENSE` file text. Every
general-web path is blocked (wq#2595 outage) or bot-challenged (Bing Turnstile, DuckDuckGo-lite
202); GitHub's and grep.app's own result pages strip repo-attribution metadata through this
session's text-extraction fetch path, leaving only description/snippet text with no resolvable
link.

The ticket's specific claim — "restricts commercial use without separate written permission (not
plain BSD-3-Clause despite appearances)" — describes a real, known pattern (a permissive-looking
license template with a commercial-use rider stacked on top, e.g. Commons-Clause-on-BSD style)
and is plausible, but is **unverified against the primary source** this session.

**Applied posture pending verification**: treat as the most conservative case the claim implies —
**external-invoke only, no bundling, no vendoring** — identical to the FAT 2.0 posture above,
until the actual `LICENSE` file is read from the real upstream repo. This costs nothing now
(OGhidra is not in `tool-catalog.json` and nothing currently depends on bundling it), so there is
no reason to wait on resolving the open item before applying the safe default.

**Open item for a future session with working search**: identify OGhidra's canonical repository
and read its `LICENSE` directly; update this document and `tool-catalog.json`'s eventual OGhidra
row (if wq#3929's P0 list adds one) with the confirmed `license`/`license_risk` values instead of
the placeholder below.

## Summary table

| Tool | License (confirmed) | Commercial-use restriction | Safe posture | Bundle into ISO? |
|---|---|---|---|---|
| EMBA | GPL-3.0 | None | external-invoke or bundle | Yes, with source-offer |
| FACT | GPL-3.0 | None | external-invoke or bundle | Yes, with source-offer |
| FAT (Attify) 2.0 | FSL-1.1-ALv2 | Yes — "Competing Use" clause | external-invoke only | No, not without Attify's written commercial license |
| OGhidra | **unverified** (ticket claims non-OSI, commercial-restricted) | Unconfirmed, treat as yes | external-invoke only | No, pending primary-source confirmation |
| (for contrast) Ghidra (NSA) — already catalogued | Apache-2.0 (`limpet/tool-catalog.json`, `"ghidra"` entry) | None | bundle is already fine | Already bundled, no change needed |

## Convention landed

`limpet/tool-catalog.json`'s `provenance.license_risk_policy` (added by this ticket) requires
every future tool entry to carry `license_risk` and `redistribution_posture` fields alongside its
existing free-text `license` field, so a future add of EMBA/FACT/FAT/OGhidra (or any other tool)
records this same analysis at catalog-entry time instead of only in a one-off audit doc. See
`limpet/tool-catalog.json:17-19` and the worked example on the `"ghidra"` entry.
