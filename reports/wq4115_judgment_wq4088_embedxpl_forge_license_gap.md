# wq#4115 — Adversarial verify-pass of wq#4088 (embedxpl/RouterXPL-Forge license/provenance audit, PR#2040)

**Verdict: CONTRADICT.** wq#4088's own deliverable
(`design/wq4088_license_audit_embedxpl_forge.md`, landed as `1bb939ca`) states as its
central finding, both in the doc's §3 heading and verbatim in the PR body: *"MPL-2.0 is
the one non-permissive license found"* / *"That MPL-2.0 component is the one real
copyleft finding."* This is false. The same repository tree the audit walked also vendors
a complete, unrelated **GPL-3.0** project — `embedxpl/native_src/cpp/oboromi/` (78 files,
upstream `0xNikilite/oboromi`, a Nintendo Switch 2 emulator written in Rust) — which the
audit never mentions. GPL-3.0 is whole-program/viral copyleft, categorically stronger than
the file-scoped MPL-2.0 obligation the audit's risk discussion leans on to justify calling
the copyleft exposure "narrow." The audit's own method (recursive tree listing, grepped for
nested `LICENSE`/`COPYING` files) was the right one and should have caught this — a
top-level `LICENSE` file sitting in the vendored directory, not a deeply-buried artifact.

A second, independently-confirmed miss (`embedxpl/native_src/cpp/cfc/LICENSE`, MIT) and a
count/location mismatch in the audit's own prose are recorded below as supporting evidence
of the same root problem — the per-component sweep on this repo was incomplete — but it is
the GPL-3.0 miss alone that changes the substance of the conclusion, not just its arithmetic.

Author: wq#4088 (`b75f1871` on branch, squash-merged to main as `1bb939ca`, 2026-10-17
01:11 UTC). Verifier: this pass (sonnet-5@high, fresh instance, same model as author per
archetype 8).

## What was shipped

One new file, `design/wq4088_license_audit_embedxpl_forge.md` (145 lines, no code, no
`tool-catalog.json` change — an evaluation doc only, matching the wq#3959 precedent this
doc explicitly cites). No executable probe exists for this ticket (`find` across `bin/`
and `foreman/` for `*4088*` turns up only event/snapshot logs, no `probe_wq4088.*`); the
closest equivalent to "run its probe" is independently re-fetching every primary source the
doc cites, which is what this pass did.

## Claims re-verified and confirmed correct

- **301 redirect**: `curl -sL -w '%{http_code} %{url_effective}'
  https://github.com/mrhenrike/RouterXPL-Forge` → `200
  https://github.com/mrhenrike/EmbedXPL-Forge`. Confirmed.
- **Repo API metadata** (`api.github.com/repos/mrhenrike/EmbedXPL-Forge`): `created_at
  2026-04-02T22:21:34Z`, `pushed_at 2026-10-16T20:18:23Z`, `stargazers_count 53`,
  `forks_count 10`, `description` text, `license.key = "other"` / `spdx_id =
  "NOASSERTION"` — every field matches the doc's §1/§2 claims exactly.
- **Top-level `LICENSE` text**: fetched `raw.githubusercontent.com/.../master/LICENSE`.
  Content is exactly as quoted — standard BSD-3-Clause three clauses, followed verbatim by
  the two non-standard sentences the doc quotes ("The above licensing was taken from the
  BSD licensing...", "...as long as you give the appropriate credit."). Confirmed, modulo
  one immaterial discrepancy — see below.
- **Recursive tree**: `git/trees/master?recursive=1` → `truncated: false`, `20827`
  entries. Matches the doc's "20,827 entries, not truncated" exactly.
- **The five named vendored licenses**: direct-fetched and spot-checked `LICENSE`/`COPYING`
  text for `cameradar` (MIT), `samsung_root` (Unlicense/public-domain, "released into the
  public domain" verbatim), `meltdown` (zlib-style — not independently re-fetched this
  pass, no reason to doubt given every other spot check held), `industroyer2`
  (Apache License Version 2.0, confirmed), `libcimbar` (Mozilla Public License Version 2.0,
  confirmed). All match the doc's table exactly.

## The contradiction: a full GPL-3.0 project vendored wholesale, never mentioned

The same tree listing used for the audit (`git/trees/master?recursive=1`) contains 20
files named `LICENSE`/`LICENSE.md`/`LICENSE.txt`/`COPYING`/`license.txt` (1 top-level + 19
nested). The audit's table accounts for exactly 7 of the 19 nested ones by path (5 named
rows + libcimbar's own `third_party_lib/` row, which lumps 10 further nested licenses
together as "each permissive"). Two nested `LICENSE` files are never mentioned anywhere in
the doc or the PR body:

| Path | License (confirmed by direct fetch) | Mentioned in audit? |
|---|---|---|
| `embedxpl/native_src/cpp/cfc/LICENSE` | MIT | No |
| `embedxpl/native_src/cpp/oboromi/license.txt` | **GPL-3.0** (full GNU GPL v3 text, "Version 3, 29 June 2007") | **No** |

`embedxpl/native_src/cpp/oboromi/` is not a stray file — it is a complete 78-file vendored
project (`CMakeLists.txt`, `Cargo.toml`/`Cargo.lock`, a Rust `core/` with `audio`, `cpu`,
`fs`, `gpu`, `nn`, `sys` modules, a GUI, CI workflows under `.github/workflows/`, its own
README and logo asset). Its README identifies it precisely: *"oboromi is a WIP emulator for
the Nintendo Switch 2... emulates an 8-core ARMv8 CPU... SM86 (NVIDIA Ampere) stub GPU"* —
upstream `0xNikilite/oboromi`, license badge `GPL v3` linking
`github.com/0xNikilite/oboromi/blob/main/LICENSE`. This has no connection to router/IoT
exploitation, EmbedXPL-Forge's stated purpose, or any of the other 18 vendored projects the
audit catalogs — it is a second, unrelated wholesale vendoring the audit's sweep simply
did not surface.

Why this matters for the audit's own conclusion, not just its count: the doc's entire §3
argument for keeping `license_risk: copyleft-source-offer` "narrow" rests on MPL-2.0 being
*the* copyleft finding, and MPL's file-scoped, Larger-Work-safe character is cited by name
as the reason the obligation is "real but narrow... just file-scoped instead of
whole-work-scoped" (contrasted explicitly against EMBA/FACT's GPL-3.0 in the wq#3959
precedent). GPL-3.0 is exactly the whole-work-scoped case that contrast was drawing a line
against — and it is present here too, just unnoticed. The final risk-tier value
(`copyleft-source-offer`) likely still holds since GPL-3.0 also carries no
commercial-use restriction (same precedent, `design/wq3959_license_audit_oghidra_emba_fact_fat.md:35-56`,
treats EMBA/FACT's GPL-3.0 the same way) — but the doc's characterization of the
*severity* and *scope* of the copyleft exposure, and therefore its comfort with the
`external-invoke`-only recommendation pending a "per-component SBOM audit," understates
what that follow-up audit would actually need to cover.

## Secondary finding: the doc's own count and location claim don't match the tree

Independent of the GPL-3.0 miss, the doc's prose says the tree "turns up 13 separate
LICENSE/COPYING files nested under `embedxpl/native_src/` and `embedxpl/malware_research/`."
Actual count under those two paths alone (excluding the top-level `LICENSE` and excluding
`samsung_root`, which is under `embedxpl/modules/exploits/`, not either named path): **18**
files (17 under `native_src/`, 1 under `malware_research/`), or **16** distinct project
directories if `zstd`'s paired `COPYING`+`LICENSE` is counted once per project. Neither 16
nor 18 is 13, and the doc's own table includes `samsung_root` as a vendored path despite
its own prose saying only `native_src/` and `malware_research/` hold these files — the
prose and the table disagree with each other over where the public-domain samsung_root
license actually sits. None of this changes the audit's listed license types; it is
further evidence the per-component sweep was undercounted rather than exhaustive.

## Minor, immaterial: LICENSE byte-count mismatch

The doc cites the top-level `LICENSE` fetch as "1693 bytes"; this pass's fetch today is
1811 bytes. The text content is identical to what the doc quotes verbatim (same two
non-standard sentences, same clause structure) — this is likely a line-ending or trailing-
whitespace difference between fetches, not a substantive change. Noted for completeness,
not a contradiction.

## Conclusion

CONTRADICT. Escalating per archetype 8 policy (fresh sonnet-5@high verifier, same model as
author → escalate opus-5@high on contradiction). Filing `[judgment]` follow-up ticket
(deps: wq#4088) for disposition — forward-fix (add the GPL-3.0 `oboromi` and MIT `cfc`
findings to the doc, re-assess whether `external-invoke` still fully covers a GPL-3.0
vendored component) vs. reopen wq#4088. Recommended fix shape: amend
`design/wq4088_license_audit_embedxpl_forge.md` §3's vendored-license table and summary to
add both missed components, explicitly contrast GPL-3.0 oboromi's whole-work copyleft
against MPL-2.0 libcimbar's file-scoped copyleft (the distinction the doc already draws
correctly for MPL vs. the wq#3959 GPL precedent, just not applied to the GPL instance found
here), and correct the "13" count — no change needed to the `license_risk` enum value
itself, since GPL-3.0 sorts into the same `copyleft-source-offer` bucket wq#3959 already
established for EMBA/FACT.
