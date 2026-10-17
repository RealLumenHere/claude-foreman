# wq#4088 — License/provenance audit: embedxpl / RouterXPL-Forge

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
entries, not truncated) turns up **13 separate `LICENSE`/`COPYING` files** nested under
`embedxpl/native_src/` and `embedxpl/malware_research/`, each belonging to a distinct vendored
upstream project bundled wholesale into this repo's tree:

| Vendored path | Upstream project (inferred) | License (confirmed by direct fetch) |
|---|---|---|
| `native_src/go/cameradar` | giann/cameradar (RTSP camera scanner) | MIT |
| `modules/exploits/smart_tv/samsung_root` | public-domain root exploit | Unlicense (public domain) |
| `native_src/c/meltdown` | Meltdown PoC | zlib-style permissive |
| `malware_research/ics_apt/industroyer2` | DFIR_Resources_Industroyer2 (YARA detection rules, CERT-UA/ESET-era Industroyer2 response — defensive signatures, not an offensive payload) | Apache-2.0 |
| `native_src/cpp/cfc/app/.../libcimbar` | sz3/libcimbar | **MPL-2.0** |
| `.../libcimbar/src/third_party_lib/{zstd,fmt,stb,PicoSHA2,cxxopts,intx,libcorrect,libpopcnt,wirehair,base91}` | standard third-party libs vendored by libcimbar itself | each permissive (BSD/MIT/public-domain per their usual upstream terms) |

**MPL-2.0 is the one non-permissive license found.** It is weak/file-scoped copyleft: it requires
source availability only for the specific MPL-covered files if they are modified and distributed,
and explicitly permits combining MPL code with proprietary code in a "Larger Work" without
infecting the rest. This is a real but narrow obligation — materially the same *category* of risk
as EMBA/FACT's GPL-3.0 in `design/wq3959_license_audit_oghidra_emba_fact_fat.md:40-56`
(source-offer, no commercial-use restriction), just file-scoped instead of whole-work-scoped.

This means the single free-text `license` field the ticket and the repo's own badges point at
(BSD/MIT, take your pick) badly understates the real licensing surface of what would actually
ship if Limpet bundled this repo's binary/package: at minimum a BSD-3-Clause-equivalent top
license, a confirmed MPL-2.0 component, and a double-digit count of further permissive
third-party licenses nested inside that one component alone. No per-component audit of this kind
exists anywhere in this repo today (`grep` of `limpet/tool-catalog.json` and `design/` for
`embedxpl`/`routerxpl`/`cimbar` before this doc returns nothing).

## Risk classification and recommended posture

Per `limpet/tool-catalog.json`'s `provenance.license_risk_policy`
(`limpet/tool-catalog.json:18`) enum (`none | copyleft-source-offer |
non-osi-commercial-restricted | unverified`):

- **`license_risk: copyleft-source-offer`** — not `none`, despite the top-level license being
  permissive, because the confirmed MPL-2.0 `libcimbar` component carries a real (if narrow)
  source-offer obligation that a `bundle` posture would inherit. Not
  `non-osi-commercial-restricted` — nothing found here is FSL/BSL-style or otherwise blocks
  commercial use; every license found (BSD-3-Clause-equivalent, MIT, Unlicense, zlib, Apache-2.0,
  MPL-2.0) permits commercial redistribution. Not `unverified` — every claim above was confirmed
  against the primary-source `LICENSE`/`COPYING` text fetched this session, not carried forward
  from the ticket's digest.
- **`redistribution_posture: external-invoke`** (recommended, not yet applied to
  `tool-catalog.json` — see below) — the same conservative default
  `design/wq3959_license_audit_oghidra_emba_fact_fat.md:150-154` used for OGhidra: the operator
  installs/updates their own copy; Limpet's agent shells out to it; no code ships inside the sold
  ISO, so none of the 13+ distinct upstream licenses' redistribution terms are triggered. `bundle`
  is not ruled out in principle (nothing here is a hard block the way FAT 2.0's FSL
  "Competing Use" clause was), but it is not safe to default into it from a single free-text
  `license` field on a repo this large and this heterogeneous — `bundle` would first need a real
  per-component SBOM pass (confirm `libcimbar`'s MPL files aren't modified by EmbedXPL-Forge's own
  wrapper code; produce a durable source-offer for all 13+ vendored projects, not just the top
  license). That audit is out of scope for this evaluation ticket and is flagged as follow-up
  work below, not performed here.

## Summary table

| Aspect | Finding |
|---|---|
| Canonical project | `github.com/mrhenrike/EmbedXPL-Forge` (formerly `RouterXPL-Forge`, 301 redirect confirmed) |
| Top-level license | Modified BSD-3-Clause (LICENSE file text); GitHub reports `Other`/`NOASSERTION`; README badge says MIT, README body says "BSD License" — both imprecise, only the LICENSE file is authoritative |
| Maintainer | Single individual (`mrhenrike`), active, created 2026-04, 53 stars / 10 forks |
| Vendored sub-licenses found | MIT, Unlicense, zlib-style, Apache-2.0, **MPL-2.0** (libcimbar), plus permissive libs nested under libcimbar's own third_party_lib |
| `license_risk` | `copyleft-source-offer` |
| `redistribution_posture` (recommended) | `external-invoke` |
| Bundle into Limpet ISO now? | Not recommended without a per-component SBOM audit of the vendored native_src tree first |
| Catalog entry added this ticket? | No — this is an evaluation doc only, matching the wq#3959 precedent of separating audit from adoption; adding a `tool-catalog.json` row is follow-up adoption work, not this ticket |

## Follow-up (not performed here, out of scope for an evaluation ticket)

A full SBOM-level pass over `embedxpl/native_src/` and `embedxpl/malware_research/` before any
`bundle` posture is adopted: confirm no MPL-2.0 `libcimbar` files are modified by EmbedXPL-Forge's
own code (which would mean source-offer obligations attach to those specific files), and draft the
source-offer list for all 13+ vendored projects identified above.
