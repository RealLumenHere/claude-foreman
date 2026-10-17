# wq#4088 — License/provenance audit: embedxpl / RouterXPL-Forge

> **Amended 2026-10-17 by wq#4124** (disposition: forward-fix) after wq#4115's verify-pass
> CONTRADICTED this doc's central §3 claim. As originally landed (`1bb939ca`) §3 asserted
> "MPL-2.0 is the one non-permissive license found"; the same tree listing the audit used also
> vendors a complete 78-file **GPL-3.0** project at `embedxpl/native_src/cpp/oboromi/`, plus a
> second unmentioned MIT component at `embedxpl/native_src/cpp/cfc/LICENSE`, and the nested
> license count was understated. §3, the risk section, the summary table and the follow-up
> below are corrected accordingly. The `license_risk` value is unchanged — see
> "Why the enum value survives" in §4. Evidence and reasoning:
> `reports/wq4115_judgment_wq4088_embedxpl_forge_license_gap.md`,
> `reports/wq4124_judgment_wq4088_license_audit_forward_fix.md`.

## Identity resolution

The ticket's name `embedxpl/RouterXPL-Forge` ("~700 modules 350 CVEs 55 vendors ICS/OT APT
chain catalog") is a single project, not two. `https://github.com/mrhenrike/RouterXPL-Forge`
returns HTTP 301 to `https://github.com/mrhenrike/EmbedXPL-Forge` (confirmed this session via
direct `curl`) — RouterXPL-Forge is the project's old name; EmbedXPL-Forge is current. The repo's
own description — *"Embedded Device Security Assessment Framework — 700 modules, 350 CVEs, 55
vendors, APT Group Engine. Covers routers, IP cameras, GPON ONTs, ISP CPEs, IoT/embedded edge"*
(`https://api.github.com/repos/mrhenrike/EmbedXPL-Forge`, fetched this session) — matches the
ticket's figures exactly, confirming this is the source project, not a fabricated value
(Tier-0 instinct 8).

**Note on drift**: the live README (`version 3.2.0`, pushed 2026-10-16T20:18Z — today) now
self-reports different, larger, and internally inconsistent numbers: a top banner claims
"2800+ active modules... 700+ CVEs... 114+ vendors", while a features subsection a few lines
down claims "625+ exploit modules". Neither matches the other, and neither matches the repo
description's "700 modules, 350 CVEs, 55 vendors" the ticket cites. Treat all of these as the
single maintainer's own unaudited self-reporting, not a verified module/CVE count.

## Redistribution-posture vocabulary (reused from `design/wq3959_license_audit_oghidra_emba_fact_fat.md`)

- **external-invoke**: Limpet shells out to a copy the operator installs themselves; no code
  ships inside the Limpet ISO/repo. Safe regardless of license.
- **bundle**: Limpet's ISO build packages the tool's own binary/package inside the sold
  appliance. Triggers the tool's redistribution terms.
- **vendor/relicense**: forking or embedding (modified) source into Limpet's own tree. Triggers
  the strictest terms, plus copyleft virality if distributed.

## 1. Top-level license — confirmed, but mislabeled in two different ways by the project itself

Primary source: `https://raw.githubusercontent.com/mrhenrike/EmbedXPL-Forge/master/LICENSE`
(fetched this session, 1693 bytes). Full text is a **modified BSD-3-Clause**: the three standard
BSD-3 clauses (retain notice in source; retain notice in binary/docs; no use of the project's
name to endorse derivatives without permission) verbatim, followed by two non-standard sentences
added by the author: *"The above licensing was taken from the BSD licensing... EmbedXPL-Forge is
provided as is, and is a royalty free open-source application. Feel free to modify, use, change,
market, do whatever you want with it as long as you give the appropriate credit."*

This repo is **not SPDX-classifiable as-is**: GitHub's own license detector reports
`"key": "other", "name": "Other", "spdx_id": "NOASSERTION"` for it
(`https://api.github.com/repos/mrhenrike/EmbedXPL-Forge`, `license` field, fetched this session) —
the added preamble/postamble breaks exact-template matching even though the operative clauses are
BSD-3-Clause. The project's own README disagrees with itself about what this is: the top-of-file
shields.io badge reads `License-MIT-green`, while the README's own `## License` section states
"BSD License — see LICENSE for details" a few hundred lines later. Neither label is the literal
truth; only the LICENSE file's text is authoritative, and it is BSD-3-Clause-equivalent: fully
permissive, attribution required, no commercial-use restriction, no copyleft, no field-of-use
carve-out. Read in isolation this would be `license_risk: none`.

## 2. Provenance — single maintainer, young, modest community size

`https://api.github.com/repos/mrhenrike/EmbedXPL-Forge` (fetched this session):
- Owner: `mrhenrike` (André Henrique, Co-Founder União Geek) — an individual account, not an
  org. No co-maintainers visible in repo metadata.
- `created_at`: 2026-04-02. `pushed_at`: 2026-10-16 (today) — actively maintained, not abandoned.
- `stargazers_count`: 53. `forks_count`: 10. Modest traction for a tool self-described at this
  scale.
- `topics` include `routersploit`, confirming its lineage (a RouterSploit-style framework); one
  absorbed sibling project, `mrhenrike/MikrotikAPI-BF`, is explicitly marked DEPRECATED in favor
  of this one ("absorbed into EmbedXPL v5.0.0") — consistent provenance trail, not a dangling
  fork.
- README's `## Legal Disclaimer`: *"EmbedXPL-Forge is intended for authorized security testing
  and research only... The authors assume no liability for misuse."* Standard dual-use-tool
  framing, no export-control or ITAR clause present. Consistent with other offensive-security
  tools already in `limpet/tool-catalog.json` (afl++, tshark) — not a blocker by itself.

## 3. The real finding: this is a ~20,827-file aggregation of dozens of separately-licensed upstream projects, not one author's codebase under one license

A full recursive tree listing (`git/trees/master?recursive=1`, fetched this session, 20,827
entries, not truncated) turns up **20 `LICENSE`/`LICENSE.md`/`COPYING`/`license.txt` blobs** —
the top-level one plus **19 nested**, belonging to **18 distinct vendored project directories**
(`zstd` carries both a `COPYING` and a `LICENSE`). 18 of the 19 nested files sit under
`embedxpl/native_src/` (17) and `embedxpl/malware_research/` (1); the 19th, `samsung_root`, sits
under `embedxpl/modules/exploits/`, not either of those paths.

*(Corrected by wq#4124. The original text said "13 separate `LICENSE`/`COPYING` files nested
under `native_src/` and `malware_research/`" — wrong on both the count and, for `samsung_root`,
the location, which the original's own table already contradicted. The counts above are from a
re-scan of the same unmodified tree JSON, reproducible with
`python3 -I tools/license-sweep/tree_license_scan.py <tree.json> embedxpl/native_src/cpp/oboromi/`
→ `20 (root: 1 nested: 19)`, `distinct nested directories: 18`, `entries under
embedxpl/native_src/cpp/oboromi/: 78`.)*

Each nested license belongs to a distinct upstream project bundled wholesale into this repo's
tree:

| Vendored path | Upstream project (inferred) | License (confirmed by direct fetch) |
|---|---|---|
| `native_src/go/cameradar` | giann/cameradar (RTSP camera scanner) | MIT |
| `modules/exploits/smart_tv/samsung_root` | public-domain root exploit | Unlicense (public domain) |
| `native_src/c/meltdown` | Meltdown PoC | zlib-style permissive |
| `malware_research/ics_apt/industroyer2` | DFIR_Resources_Industroyer2 (YARA detection rules, CERT-UA/ESET-era Industroyer2 response — defensive signatures, not an offensive payload) | Apache-2.0 |
| `native_src/cpp/cfc` | the `cfc` wrapper app itself (parent of the vendored libcimbar) | MIT (added by wq#4124) |
| `native_src/cpp/cfc/app/.../libcimbar` | sz3/libcimbar | **MPL-2.0** |
| `.../libcimbar/src/third_party_lib/{zstd,fmt,stb,PicoSHA2,cxxopts,intx,libcorrect,libpopcnt,wirehair,base91,concurrentqueue}` | standard third-party libs vendored by libcimbar itself | each permissive (BSD/MIT/public-domain per their usual upstream terms) — 11 directories, not the 10 originally listed (`concurrentqueue` was omitted) |
| `native_src/cpp/oboromi` | `0xNikilite/oboromi` — a WIP Nintendo Switch 2 emulator, vendored whole (78 files: `CMakeLists.txt`, `Cargo.toml`/`Cargo.lock`, a Rust `core/` with `audio`/`cpu`/`fs`/`gpu`/`nn`/`sys`, a GUI, its own `.github/workflows/` CI, README, logo) | **GPL-3.0** (added by wq#4124) |

**Two copyleft licenses are present, not one.** The original version of this doc stated "MPL-2.0
is the one non-permissive license found" — that was wrong, and it was the load-bearing sentence
under this heading. Corrected:

- **GPL-3.0 (`native_src/cpp/oboromi/license.txt`)** — full GNU GPL v3 text, "Version 3,
  29 June 2007", 674 lines, re-fetched by wq#4124 from
  `raw.githubusercontent.com/.../master/embedxpl/native_src/cpp/oboromi/license.txt`; the
  subtree's own README carries a `license-GPL v3` badge pointing at
  `github.com/0xNikilite/oboromi/blob/main/LICENSE`. This is **whole-work copyleft**: any work
  that is a derivative of, or statically/dynamically linked into one program with, this code must
  itself be GPL-3.0 when distributed. It is the strongest obligation in this repo.
- **MPL-2.0 (`libcimbar`)** — weak/file-scoped copyleft: source availability only for the
  specific MPL-covered files if they are modified and distributed, and explicitly permits
  combining MPL code with proprietary code in a "Larger Work" without infecting the rest.

So the copyleft exposure here is **not** uniformly "narrow." The MPL half is narrow for exactly
the reason given above; the GPL-3.0 half is the whole-work case that
`design/wq3959_license_audit_oghidra_emba_fact_fat.md:35-56` (EMBA/FACT) analyses, and the
original text cited that precedent only to contrast *against* it — while an instance of it sat
unnoticed in the same tree.

**Provenance note on `oboromi` (wq#4124):** a Nintendo Switch 2 emulator has no connection to
router/IoT/ICS exploitation, this repo's stated purpose, or any of the other 17 vendored
projects. It is a second unrelated wholesale vendoring, and it carries non-license risk of its
own: console-emulator projects are a standing takedown target, so a bundled copy inherits both
the GPL-3.0 obligation and the chance that upstream disappears from under it. Whatever posture is
chosen, this subtree is the first thing to excise.

This means the single free-text `license` field the ticket and the repo's own badges point at
(BSD/MIT, take your pick) badly understates the real licensing surface of what would actually
ship if Limpet bundled this repo's binary/package: at minimum a BSD-3-Clause-equivalent top
license, a confirmed GPL-3.0 project vendored whole, a confirmed MPL-2.0 component, and a
double-digit count of further permissive third-party licenses nested inside that one component
alone. No per-component audit of this kind
exists anywhere in this repo today (`grep` of `limpet/tool-catalog.json` and `design/` for
`embedxpl`/`routerxpl`/`cimbar` before this doc returns nothing).

## Risk classification and recommended posture

Per `limpet/tool-catalog.json`'s `provenance.license_risk_policy`
(`limpet/tool-catalog.json:18`) enum (`none | copyleft-source-offer |
non-osi-commercial-restricted | unverified`):

### Why the enum value survives (wq#4124)

The GPL-3.0 finding does **not** move `license_risk`. The enum's only stronger neighbour is
`non-osi-commercial-restricted`, and GPL-3.0 carries no commercial-use restriction — it
explicitly permits distribution "whether gratis or for a fee"; the same precedent that put
EMBA/FACT's GPL-3.0 in `copyleft-source-offer`
(`design/wq3959_license_audit_oghidra_emba_fact_fat.md:35-56`) applies unchanged here. What the
finding moves is the *severity within* that bucket, and therefore the bundle precondition below:
a GPL-3.0 subtree is a materially harder thing to clear than a file-scoped MPL one.

- **`license_risk: copyleft-source-offer`** — not `none`, despite the top-level license being
  permissive, because the confirmed GPL-3.0 `oboromi` project (whole-work copyleft) and the
  confirmed MPL-2.0 `libcimbar` component (file-scoped) both carry source-offer obligations that
  a `bundle` posture would inherit. Not
  `non-osi-commercial-restricted` — nothing found here is FSL/BSL-style or otherwise blocks
  commercial use; every license found (BSD-3-Clause-equivalent, MIT, Unlicense, zlib, Apache-2.0,
  MPL-2.0, GPL-3.0) permits commercial redistribution. Not `unverified` — every license *type*
  claimed above was confirmed against primary-source `LICENSE`/`COPYING` text, the two wq#4124
  additions by its own re-fetch. The sweep's *completeness*, by contrast, was not verified when
  this doc first landed and was wrong; see the amendment banner.
- **`redistribution_posture: external-invoke`** (recommended, not yet applied to
  `tool-catalog.json` — see below) — the same conservative default
  `design/wq3959_license_audit_oghidra_emba_fact_fat.md:150-154` used for OGhidra: the operator
  installs/updates their own copy; Limpet's agent shells out to it; no code ships inside the sold
  ISO, so none of the 18 distinct upstream licenses' redistribution terms are triggered — the
  GPL-3.0 subtree included, since GPL obligations attach to distribution, and an
  operator-installed copy distributes nothing of ours. This posture is unchanged by wq#4124's
  findings; it was already the conservative default, and it absorbs the GPL-3.0 miss without
  further work. `bundle` is still not ruled out in principle (nothing here is a hard block the
  way FAT 2.0's FSL "Competing Use" clause was), but wq#4124 raises its bar: it is not merely a
  paperwork SBOM pass. `bundle` would first need, in order of difficulty —
  1. **Dispose of `native_src/cpp/oboromi/` (GPL-3.0).** Either excise the subtree from whatever
     artefact ships (simplest, and independently advisable per the provenance note in §3), or
     establish that it is mere aggregation rather than one work with EmbedXPL-Forge's own code —
     plausible on its face (it is a self-contained Rust/CMake program with no visible call path
     from the Python framework) but it must be *shown*, not assumed, and if it is linked or
     invoked as part of a combined work the whole combined work becomes GPL-3.0 on distribution.
  2. **Confirm `libcimbar`'s MPL-2.0 files are unmodified** by EmbedXPL-Forge's own wrapper code,
     else source-offer obligations attach to those specific files.
  3. **Produce a durable source-offer list** covering all 18 vendored projects, not just the top
     license.
  That work is out of scope for this evaluation ticket and is flagged as follow-up below.

## Summary table

| Aspect | Finding |
|---|---|
| Canonical project | `github.com/mrhenrike/EmbedXPL-Forge` (formerly `RouterXPL-Forge`, 301 redirect confirmed) |
| Top-level license | Modified BSD-3-Clause (LICENSE file text); GitHub reports `Other`/`NOASSERTION`; README badge says MIT, README body says "BSD License" — both imprecise, only the LICENSE file is authoritative |
| Maintainer | Single individual (`mrhenrike`), active, created 2026-04, 53 stars / 10 forks |
| Vendored sub-licenses found | MIT (cameradar, cfc), Unlicense, zlib-style, Apache-2.0, **MPL-2.0** (libcimbar, file-scoped copyleft), **GPL-3.0** (oboromi, whole-work copyleft), plus 11 permissive libs nested under libcimbar's own third_party_lib — 20 license blobs / 18 distinct vendored projects in total |
| `license_risk` | `copyleft-source-offer` (unchanged by wq#4124 — GPL-3.0 sorts into the same bucket; severity within it is higher than originally written) |
| `redistribution_posture` (recommended) | `external-invoke` (unchanged) |
| Bundle into Limpet ISO now? | No. Blocked first on disposing of the GPL-3.0 `oboromi` subtree (excise, or prove mere aggregation), then the MPL/SBOM pass |
| Catalog entry added this ticket? | No — this is an evaluation doc only, matching the wq#3959 precedent of separating audit from adoption; adding a `tool-catalog.json` row is follow-up adoption work, not this ticket |

## Follow-up (not performed here, out of scope for an evaluation ticket)

A full SBOM-level pass over `embedxpl/native_src/`, `embedxpl/malware_research/` and
`embedxpl/modules/exploits/` before any `bundle` posture is adopted — the three paths the 19
nested licenses actually span. Scope, per the ordered bar in the risk section above: dispose of
the GPL-3.0 `oboromi` subtree, confirm no MPL-2.0 `libcimbar` files are modified by
EmbedXPL-Forge's own code (which would mean source-offer obligations attach to those specific
files), and draft the source-offer list for all 18 vendored projects identified above.

Start that pass from a machine sweep of the tree listing rather than a hand-reconciled table —
`tools/license-sweep/tree_license_scan.py` is the one wq#4124 used, and the gap it closed (a 78-file GPL-3.0
project missed by a sweep that enumerated the right tree) is exactly what hand-reconciliation
loses on a 20,827-entry repo.
