# wq#4124 — Judgment on wq#4115's CONTRADICT of wq#4088 (embedxpl/EmbedXPL-Forge license audit)

**Disposition: FORWARD-FIX. wq#4088 stays closed; its deliverable is amended in place.**
wq#4115's contradiction is upheld, re-verified here from primary sources rather than accepted on
its say-so. The amendment landed on this branch in
`design/wq4088_license_audit_embedxpl_forge.md`.

Judge: this pass (opus-5@high, escalated per archetype 8 after a sonnet-5@high verifier
contradicted a sonnet-5@high author). Author: wq#4088 (`1bb939ca`). Verifier: wq#4115
(`dfbbd98c`, PR#2057, `reports/wq4115_judgment_wq4088_embedxpl_forge_license_gap.md`).

## 1. The contradiction is real (re-verified independently)

wq#4115 reported paths and license types. A judgment that only repeats a verifier's claims adds
nothing, and per Tier-0 instinct 8 a path nobody has re-fetched is a path that might not exist.
All of the load-bearing facts were re-fetched this pass:

| Claim | Re-verification | Result |
|---|---|---|
| The tree the audit used holds more license files than the audit reconciled | `git/trees/master?recursive=1` re-fetched (7,154,401 bytes), re-scanned with `tools/license-sweep/tree_license_scan.py` | `truncated: false`, 20,827 entries, matching the audit. **20** `LICENSE`/`LICENSE.md`/`COPYING`/`license.txt` blobs: 1 at the root, 19 nested across 18 distinct vendored project directories |
| `embedxpl/native_src/cpp/oboromi/license.txt` is GPL-3.0 | direct fetch of the raw blob | Confirmed. Full GNU GPL v3, "Version 3, 29 June 2007", 674 lines |
| `oboromi` is a whole vendored project, not a stray file | same tree scan, path-prefix count | Confirmed, **78 entries** under `embedxpl/native_src/cpp/oboromi/` |
| `oboromi` is unrelated to the tool's purpose | its own vendored `README.md`, fetched | Confirmed. A `license-GPL v3` badge pointing at `github.com/0xNikilite/oboromi/blob/main/LICENSE`, and a self-description as a WIP Nintendo Switch 2 emulator. Nothing to do with routers, IoT or ICS |
| `embedxpl/native_src/cpp/cfc/LICENSE` is MIT and unmentioned | direct fetch, plus `grep` of the as-landed audit | Confirmed. MIT License text; the path appears nowhere in `1bb939ca`'s doc |

So the audit's §3 sentence, *"MPL-2.0 is the one non-permissive license found"*, was false when
written, and false about the most consequential component in the sweep. That sentence carried
weight twice over. It headed the section, and it supplied the premise for the risk narrative's
"real but narrow" reading of the copyleft exposure.

Two corrections to wq#4115's own arithmetic, neither of which touches its verdict. Scoped to
`native_src/` plus `malware_research/`, the nested count is 18 files over 17 distinct project
directories, not the 16 it reported. And the audit's `third_party_lib` row lumped 10 libraries
where the tree has 11: `concurrentqueue` went unmentioned too, a third missed license file, this
one permissive.

## 2. Why forward-fix rather than reopen wq#4088

Reopening is for a deliverable whose method or conclusion has to be rebuilt. Neither applies.

**The method was right.** The audit walked the correct, complete, untruncated tree and grepped it
for nested license files, the same procedure that surfaces `oboromi` immediately. The defect is in
reconciling that listing by hand: 7 of the 19 nested files reached the table and the rest were
summarised rather than named. A transcription failure inside a sound method, repairable by
finishing the enumeration.

**The conclusion holds at the level the catalog consumes.** `license_risk` is a four-value enum
(`limpet/tool-catalog.json:18`). The only value above `copyleft-source-offer` is
`non-osi-commercial-restricted`, and GPL-3.0 does not reach it, because it permits distribution
"whether gratis or for a fee". That is exactly why
`design/wq3959_license_audit_oghidra_emba_fact_fat.md:35-56` put EMBA/FACT's GPL-3.0 in
`copyleft-source-offer`. `redistribution_posture: external-invoke` survives untouched as well, for
a reason worth stating: GPL obligations attach to distribution, and under `external-invoke` the
operator installs their own copy while Limpet ships none of it. The conservative default absorbed
the miss. No downstream artefact carries a wrong value today, since the audit added no
`tool-catalog.json` row (confirmed: no `embedxpl` key in the catalog), so nothing has to be
unwound.

**What the miss changed is already in the doc's own follow-up scope**, which called for a
per-component SBOM pass before any `bundle` posture. The GPL-3.0 finding makes that pass harder
and reorders it. It does not invent a new workstream.

Reopening would reproduce a correct identity resolution, a correct provenance section and a
correct top-level license analysis in order to fix one table and one sentence. Forward-fix with
the verifier's evidence attached is cheaper and a more honest record.

## 3. What the amendment changed

All in `design/wq4088_license_audit_embedxpl_forge.md`:

- An amendment banner at the top naming wq#4115's contradiction, the false sentence as originally
  landed, and both evidence reports, so nobody reads the doc without learning it was corrected.
- §3 table: rows for `native_src/cpp/cfc` (MIT) and `native_src/cpp/oboromi` (GPL-3.0, with the
  78-file subtree inventoried); the `third_party_lib` row corrected to 11.
- §3 prose: "MPL-2.0 is the one non-permissive license found" replaced with the two-copyleft
  finding, stating GPL-3.0's whole-work reach against MPL-2.0's file-scoped reach. The doc already
  drew that distinction correctly for the wq#3959 GPL precedent, and never applied it to the GPL
  instance sitting in its own tree.
- §3 counts: 13 becomes 20 blobs, 19 nested, 18 distinct projects. The location claim is fixed as
  well, since `samsung_root` sits under `modules/exploits/`, which the doc's own table had already
  contradicted.
- A provenance note on `oboromi`. Beyond licensing, a console emulator vendored into an IoT
  exploitation framework is a standing takedown target, so a bundled copy inherits that risk too.
- Risk section: a "Why the enum value survives" subsection carrying the enum reasoning above, so
  the unchanged value reads as a judgment rather than an oversight. The `bundle` precondition is
  rewritten from a flat SBOM pass into an ordered bar whose first item is disposing of the GPL-3.0
  subtree, either by excising it or by showing it is mere aggregation. That is plausible on its
  face, since it is a self-contained Rust/CMake program with no visible call path from the Python
  framework, but it has to be shown rather than assumed.
- Summary table and follow-up: counts, license list and bundle answer updated. The follow-up now
  spans the three paths the licenses actually occupy, and points at the scan tool.

## 4. Durable lesson

A per-component license sweep that lists the tree correctly and then reconciles it by hand does
not scale past a few dozen entries, and its failure mode is silent. The sweep's own output had
`oboromi/license.txt` on screen while the conclusion said there was one copyleft component. Let
the machine do the reconciliation instead: enumerate every hit, then account for each one by name,
rather than summarising the tail as "each permissive."

`tools/license-sweep/tree_license_scan.py` is the minimal form of that. It takes a saved
`git/trees/<sha>?recursive=1` dump, prints the truncation flag first so a tree that cannot be
swept says so, lists every license-shaped blob, and counts them. A future audit's count is then a
machine number rather than prose. Probed outside the repo it was written against, per wq#2630: on
`e-m-b-a/emba` rev-pinned at `bddbea3cc2cd7652a6b15e9aaf25ed34024f5b10` it reports `979 entries`
and `license-shaped blobs: 1 (root: 1 nested: 0)`, the single GPL-3.0 top-level `LICENSE` that
`design/wq3959_license_audit_oghidra_emba_fact_fat.md:35-56` audited by hand. Anything in the tool
specific to EmbedXPL-Forge's paths would have gotten that wrong. Logged as
`/home/lumen/kb/ISSUE-LOG.md` row 1383.

## 5. Residual work (not this ticket)

The SBOM pass itself, if a `bundle` posture is ever considered for this tool. It is recorded in
the amended doc's follow-up section with the ordered bar above. No catalog row, no posture change,
and no further correction to wq#4088's deliverable is outstanding.
