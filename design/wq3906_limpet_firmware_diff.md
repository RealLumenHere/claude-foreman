# wq#3906 — LIMPET firmware-diff plugin (whole-image comparison across versions) — design

Two firmware versions in → one structured diff report out. Emerging Gap #10 from the RE
Tool Catalog gap analysis: BinDiff compares two *individual binaries*, but no open-source
tool diffs two whole *firmware images* — tells you which files appeared, vanished, or
changed, and for the changed executables, which functions moved. This document designs
that image-level differ as a Limpet plugin: walk two workspace extractions → classify
every file (added/removed/unchanged/modified) → run a per-file differ on the modified set
(BinDiff for executables, text/binary deltas otherwise) → emit `firmware-diff.json` into
the workspace, committed to `workspace.git`.

**Design-only.** Nothing here installs software or registers a tool. Every external tool
named below (BinDiff, the BinExport exporter, Diaphora) enters through the existing
adoption gate (`TOOL_REGISTRY.md:4`, trust tiers per
`design/wq3806_limpet_architecture.md` §1.2). This is explicitly a **future plugin, not
v1 core** (the ticket's own framing): it is authored at `community` and stays inert until
promoted. Companion artifact in this branch: `limpet/examples/fw-diff.toml`, a validating
plugin manifest for the differ, exercised by the example-manifest test in
`limpet/test_limpet_cli.py`.

## 0. Constraints and evidence posture (read first)

1. **Prior-art search ran degraded** (same backend outage wq#3900 hit the same day). The
   mandatory fleet-search step ran 2026-10-14: the web backend was fully down (Brave/DDG/
   Google-CSE/Startpage all rate-limited or CAPTCHA'd, no `BRAVE_API_KEY` — pre-existing
   wq#2595), the GitHub backend returned 401 (no usable token), and reddit responded
   empty. External tool facts below (BinDiff is Apache-2.0 and ships a Ghidra BinExport
   exporter; its result file is a SQLite DB; Diaphora exists as an alternative) therefore
   come from model knowledge, are labeled **[verify-at-adoption]**, and MUST be
   re-verified during the TOOL_REGISTRY go/no-go before any install. The design is
   structured so a wrong external fact invalidates one *stage's tool choice*, never the
   plugin shape (file-walk + classify + per-file differ + report).
2. **Fleet-internal prior art is real and should anchor the contract.** The fleet's REA
   MCP already exposes `compare_artifacts`, `compare_application_versions`, and
   `compare_functions` — version-to-version comparison primitives. This plugin is the
   *firmware-image* case REA does not cover (REA compares app bundles / individual
   artifacts, not two unpacked firmware trees), and it should mirror REA's output shape
   where it can so Limpet diff reports read like the rest of the fleet's compare output.
   That is the "already solved it" lead the prior-art doctrine asks for — found inside the
   fleet toolset rather than on the web.
3. **Fits the wq#3806 architecture, doesn't fork it.** Inputs are two workspace
   extractions (`design/wq3806_limpet_architecture.md` §5: `target/` holds the unpacked
   image, `workspace.git` already version-tracks every analysis step). The differ depends
   on the `ghidra` plugin for BinExport and consumes the firmware-extraction output that
   skill already produces (unblob tree). Output lands in the §5 `evidence/` dir and is
   committed to `workspace.git`. BinDiff runs in the §5 per-workspace container
   (no network needed — it is a pure static differ). No new infrastructure classes.

## 1. Where the two versions live (workspace git is the foundation)

The ticket's key insight: **workspace git tracking provides the natural foundation.** Two
layouts are supported, same differ underneath:

- **One workspace, two commits** (preferred for an upgrade investigation): the operator
  extracts firmware v1 into `target/`, commits, extracts v2 over it, commits again. The
  differ takes `diff_versions(workspace, rev_a, rev_b)` and reads both trees straight out
  of `workspace.git` — path-stable tracking across versions is exactly what git gives for
  free, so file matching starts from git's own rename/content tracking rather than a
  hand-rolled walk.
- **Two sibling workspaces** (for independently-acquired images): `diff_firmware(ws_a,
  ws_b)` diffs `ws_a/target/` against `ws_b/target/`.

Both resolve to the same primitive: two extraction roots on disk. Everything below is
defined on those two roots; the git layer is how we *obtain* them cheaply and
reproducibly, not a second code path.

## 2. Pipeline overview

```
[1 enumerate]  walk both extraction roots → relative-path file inventory + sha256 each
[2 classify]   set-diff the two inventories by path:
               added / removed / unchanged(hash eq) / modified(path eq, hash differ)
               + moved (hash eq, path differ) detected across the removed/added sets
[3 type]       for each modified pair, sniff file type (ELF/PE / text / opaque blob)
[4 differ]     ELF|PE  → BinExport(ghidra) each side → BinDiff → parse .BinDiff SQLite
               text    → unified line diff (added/removed lines, hunks)
               opaque  → size delta + block-level changed-ratio (no false precision)
[5 report]     firmware-diff.json in workspace evidence/ (schema §5), committed
[6 consume]    summary rollup for the agent/operator; per-file detail on demand
```

The expensive step (4, BinDiff, which must disassemble and decompile both sides) runs
**only on `modified` executables** — stage 2's hash gate is what makes a thousands-of-file
image tractable: unchanged files never reach a differ at all. Each stage writes its
artifact, so a run is replayable and the heavy stage is resumable per-file.

## 3. Stage 2 — classification (the part that must be exactly right)

File identity is `(relative-path, sha256)`. Against the two inventories:

| class | condition | differ run? |
|---|---|---|
| `unchanged` | same path, same hash | no |
| `modified` | same path, different hash | yes (stage 4) |
| `added` | path only in B | no (listed) |
| `removed` | path only in A | no (listed) |
| `moved` | hash in both, path differs | no (noted as rename) |

`moved` is resolved as a second pass over the `added`∖`removed` sets by hash, so a file
relocated between versions (common when a vendor reorganizes `/usr/bin`) is reported as a
rename, not as one deletion plus one unrelated addition — that single correction is most
of what makes an image diff readable. Symlinks, device nodes, and the extraction tool's
own carve-artifact directories are classified by a small type pass (stage 3) and excluded
from the executable differ; they still appear in the inventory so the diff is complete.

**Falsifiable boundary for the acceptance probe:** the classifier must be exercised on a
fixture containing at least one of *each* class, including a `moved` file whose hash is
identical across a path change — a probe drawn only from "modified" pairs would confirm
and never falsify (wq#2630). The committed test fixture (§6) pins that.

## 4. Stage 4 — per-file differ

**Executables (the BinDiff path).** BinDiff does not read a raw binary; it diffs two
`.BinExport` files produced by a disassembler. BinDiff 8 is Apache-2.0 and ships a Ghidra
BinExport exporter **[verify-at-adoption]**, and Limpet already carries a `ghidra` plugin
(`limpet/examples/ghidra.toml`) — so the chain is: `ghidra` headless export each side →
`bindiff` → the result is a `.BinDiff` **SQLite** database **[verify-at-adoption]** whose
`function` table carries per-function `similarity`/`confidence` and match flags. The
plugin parses that DB (read-only, `sqlite3`) into the report, never re-implements the
matching. Per-pair output: whole-binary similarity, and `{matched, changed, added,
deleted}` function counts with the top-N most-changed functions by similarity delta.
Diaphora is the fallback differ **[verify-at-adoption]** if BinExport/BinDiff adoption
stalls; stage 4 is written so the differ backend is swappable and named in the report.

**Text files.** A unified line diff (config, init scripts, `/etc`), summarized as
hunk count + added/removed line counts, with the full patch stored beside the report so
the agent can open it without re-diffing.

**Opaque blobs** (kernels, packed rootfs images the extractor could not unpack further,
keys, resource blobs). No pretend function-level analysis: report size delta and a
block-level changed-ratio (fixed-size block hashing, count differing blocks / total).
A kernel that the operator wants disassembled is re-extracted into its own workspace and
diffed there — recursion is an operator decision, not an automatic descent, because
unbounded recursion into every nested image is how an image diff turns into an all-night
job. This is a deliberate v1 boundary, flagged in the report as `not_deep_diffed`.

## 5. Stage 5 — the diff report (the deliverable)

`workspace/evidence/firmware-diff.json`, committed to `workspace.git`:

```json
{
  "schema": "limpet.firmware-diff/1",
  "a": {"workspace": "...", "rev": "<git-sha-or-null>", "root_sha256": "..."},
  "b": {"workspace": "...", "rev": "<git-sha-or-null>", "root_sha256": "..."},
  "summary": {"added": 12, "removed": 3, "modified": 41, "moved": 5,
              "unchanged": 1180, "binaries_bindiffed": 29,
              "mean_binary_similarity": 0.94},
  "files": [{
    "path": "usr/sbin/httpd",
    "status": "modified",
    "type": "elf",
    "sha256": {"a": "...", "b": "..."},
    "binary": {"differ": "bindiff", "similarity": 0.87, "confidence": 0.95,
               "functions": {"matched": 412, "changed": 23, "added": 4, "deleted": 1},
               "top_changed": [{"name": "handle_request", "similarity": 0.41}],
               "bindiff_db": "evidence/bindiff/usr_sbin_httpd.BinDiff"},
    "re_notes": ["handle_request rewritten — primary review target"]
  }, {
    "path": "etc/config/network", "status": "modified", "type": "text",
    "text": {"hunks": 2, "added_lines": 6, "removed_lines": 1,
             "patch": "evidence/textdiff/etc_config_network.patch"}
  }],
  "moved": [{"from": "bin/busybox", "to": "usr/bin/busybox", "sha256": "..."}],
  "not_deep_diffed": ["boot/uImage"],
  "run": {"plugin_version": "...", "timestamp": "...",
          "ghidra_version": "...", "bindiff_version": "..."}
}
```

Properties that matter: a `summary` rollup an agent reads first (is this a security patch
or a feature release?), **provenance** on every binary verdict (which differ, which tool
versions, a pointer to the raw `.BinDiff` DB so a skeptic can re-open it), `moved` and
`not_deep_diffed` as **first-class** rather than silently folded in, and `re_notes`
carrying the one line that routes the operator ("`handle_request` rewritten — review
here"). The schema version is in-band so a consumer can reject a report it doesn't
understand.

## 6. Integration

- **Limpet plugin.** `fw-diff` (tool kind — manifest `limpet/examples/fw-diff.toml`)
  exposes via the MCP meta-server: `diff_firmware(workspace_a, workspace_b)` and
  `diff_versions(workspace, rev_a, rev_b)` (full image diff), and `diff_binary(path_a,
  path_b)` (single-pair BinDiff, for when the operator already knows the two files). The
  split lets the agent re-run one binary's diff without re-walking the image. Health check
  = `fw-diff selftest` against a committed two-tree fixture (so health can genuinely fail,
  not just "binary exists").
- **Dependencies.** Declares `ghidra >= 11.0` (BinExport) and `bindiff` (the differ) in
  the manifest `[dependencies]` table, so the registry leaves `fw-diff` **blocked** until
  both are installed and enabled (wq#3810 lifecycle step 5). The image-walk/classify/
  report stages run with zero differs present — degraded mode reports inventory classes
  only — which is what keeps the dependency gate honest rather than decorative.
- **Trust tiers.** The plugin itself is fleet-authored (`community` now, `vetted`/`core`
  once implemented and go/no-go'd). BinDiff, BinExport, and Diaphora are external
  adoptions, authored `community`, promoted per `TOOL_REGISTRY.md:40-46` after their
  **[verify-at-adoption]** checks (license confirm, source read, pin a rev).
- **Workspace containment.** Runs in the §5 per-workspace container with both extraction
  roots bind-mounted read-only and no network — a static differ needs neither write access
  to `target/` nor egress. Only `evidence/` is writable.

## 7. Evaluation plan (for the implementation ticket's acceptance)

A **golden two-tree fixture**: two small synthetic extraction trees committed under the
plugin's tests, hand-constructed to contain one file of every stage-2 class — an added
file, a removed file, an unchanged file, a text `modified` file, a `moved` file (identical
hash, changed path), and one small `modified` ELF pair built from two revisions of a
one-function C program (so BinDiff has a real, known function change to find). Metrics,
measured by a script not by eyeballing:

- **classification accuracy**: every fixture file lands in its known class (this is the
  core correctness claim and includes the `moved`-not-add+remove case);
- **binary-diff fidelity**: the known-changed function in the ELF pair appears in
  `changed`, the known-added one in `added` — gated behind the differ being installed, so
  the metric runs degraded-skip when BinDiff isn't present rather than failing spuriously;
- **completeness**: `len(files) + len(moved)` accounts for every file in the union of both
  inventories (nothing silently dropped).

Pass bar is set by the implementation ticket against a first baseline; this design fixes
the *metrics*, not the numbers. The fixture also powers the plugin health check (§6).

## 8. Build order (for the implementation ticket, not this one)

1. Enumerate + classify + report on **two directory roots** (stages 1–3, 5), text/opaque
   differs only — no BinDiff dependency. Golden fixture + metrics script in the same PR.
   This alone closes most of the gap: "what changed between these two images" is answered.
2. `diff_versions` git-rev source (read two revs out of `workspace.git`) on top of the
   directory-root primitive.
3. BinDiff path behind the go/no-go gate: `ghidra` BinExport + `bindiff` adoption, SQLite
   result parsing. Plugin fully functional (degraded) before this lands.
4. `diff_binary` single-pair tool + `top_changed`/`re_notes` enrichment.
5. v2 slots: Diaphora backend option; bounded recursive descent into one nested blob;
   cross-architecture diff caveats (BinDiff similarity is noisier across arches — flag,
   don't hide).

## 9. Open items

- wq#2595 (Brave key) + GitHub token blocks clean re-verification of every
  **[verify-at-adoption]** fact (BinDiff license/format, BinExport Ghidra support,
  Diaphora); the adoption go/no-gos should not start before fleet-search web and github
  work.
- Whether to vendor BinDiff's result-DB schema read as a pinned helper or depend on a
  BinDiff Python binding — decide at adoption after reading what BinDiff 8 actually ships.
- Cross-architecture and endianness-mismatch diffs (v1 compares like-for-like by path;
  a vendor that switches SoC between versions is out of v1 scope — report it, route to a
  manual session).
