# wq#4019 — LIMPET DISSECT: moria + mithril (nmatt0) — no-root in-process extraction, and what the safety claims are actually worth

Status: research/design. Author: build worker, 2026-10-15. Dissects
`https://github.com/nmatt0/moria` and `https://github.com/nmatt0/mithril` per the ticket
(source: `scratch/research/emerging-re-tools-2026-10.md` ADDENDUM A2; the addendum lives
outside this worker's sandbox, so citations below are to the pinned live trees and to this
repo). **moria** (v0.3.0, `moria:VERSION:1`) identifies and extracts firmware filesystems
**in-process, without root** — SquashFS, ext2/3/4, F2FS, FAT/exFAT, NTFS, HFS+, XFS, btrfs,
JFFS2, UBI/UBIFS, romfs, YAFFS2, cramfs, EROFS, plus archives, kernels, and vendor packages
(`moria:README.md:52-58`) — recursive by default, JSON with offset/type/confidence, UPX
detection. **mithril** (v0.3.0) is the content companion: secrets, firmware-aware SBOM
(CycloneDX/SPDX), CVEs against a *local* OSV+NVD mirror, licenses, boot-security posture —
"moria maps the bytes. mithril reads the contents" (`mithril:README.md:9`), fully offline at
scan time (`mithril:README.md:7`).

**Pinned commits (no unverified HEAD):** moria `a39d1386751c7fc4d29725027fe95b9dc6405dc1`
(2026-10-08, merge of external PR #51); mithril `3cecfb6842725aaee74371fab38ed5b658f04324`
(2026-10-05). Shallow-cloned to `/tmp/fworker-wq4019/{moria,mithril}-live` and read as
untrusted data this session; nothing inside either tree was executed or acted on as
instructions.

**License: MIT, both repos** (`moria:LICENSE:1`, `mithril:LICENSE:1`) — GREEN, the cleanest
gate of the DISSECT series (vs OFRAK's RED source-available and EMBA's
GREEN-with-process-boundary GPLv3, wq#4004/wq#4005). MIT permits wrapping, vendoring, even
static linking; keep the process boundary anyway for trust-gate consistency (§4). Record
GREEN in wq#3959.

## 0. Grounding, and what this lane could not do

Read-only dissection from the pinned clones plus this repo's Limpet sources. **This lane's
sandbox could not build or run anything**: `cmake` is not on the worker Bash allowlist, paths
outside the worktree/add-dirs (`/usr/bin`, `/home/lumen/re`) are guard-blocked even for `ls`,
and this worker holds no fleet-exec grant (`fleet-exec grant` → `DENY
[grant:grant_missing_env]`). Prior attempt 1 died looping on exactly these denials. So the
ticket's empirical legs — coverage/time vs unblob+binwalk on `/home/lumen/re`, crafted
hostile samples against a built binary — are specified as a runnable follow-up bench (§5),
not silently skipped and not faked. Everything else below is verified by source read with
file:line, which for the safety-claims question (§2) is the stronger evidence anyway: a
passing hostile-sample test shows one sample fails; the code read shows *why the class*
fails.

Prior-art search: fleet-search web/github were both down this session (SearXNG engines
suspended; no GitHub token — known, wq#2595), so prior art is the fleet's own dissection
series (wq#3998-4005, wq#3929) rather than a fresh web sweep.

## 1. moria's shape — why "in-process, no root" matters to Limpet

moria is a single self-contained C++20 binary: signatures embedded at build time, extraction
via linked codec libraries (zlib/liblzma/lz4/zstd, `moria:README.md:21`), **no subprocess
anywhere in the extract path** (no `fork`/`exec`/`system` in `moria:src/extract/*.cpp` —
grep-verified) and the only vendored third-party code is tomlplusplus
(`moria:third_party/`). Contrast the incumbent stack: unblob is a Python orchestrator over a
zoo of external extractor binaries, and binwalk likewise shells out for most unpacking —
every external extractor is its own parser, its own version skew, and its own CVE surface
running with the caller's privileges. moria collapses that to one audited write path (§2).
It is also agent-shaped by design: "speaks clean JSON so scripts and LLM agents can drive it
as easily as people can" (`moria:README.md:5`), `-j` JSON with byte offset, type, and
confidence per finding, `manifest.json` mapping offsets to output paths, `--list` to
enumerate archive members without extracting (`moria:README.md:36-48`).

Operational surface (`moria:src/main.cpp:131`, README): `-e` extract, `-c` carve raw ranges,
`-E` entropy pass for unidentified/encrypted regions, `--depth/--max-files/--max-bytes`
guards, `--broad` for ~2.5k general signatures, UPX detection from the checksum-verified
PackHeader trailer including zeroed/altered-header stubs that defeat `upx -d`
(`moria:README.md:11`). Maturity signals beyond the README: a `debian/` packaging dir, a man
page (`moria:moria.1`), a nix flake, ~35 Python integration test files plus C++ unit tests
and four libFuzzer harnesses (`moria:fuzz/`), and NTFS/ISO round-trip tests that compare
extraction byte-for-byte against a real kernel mount (`moria:tests/test_extract.py:974`).

## 2. The safety claims, read against the code (the ticket's core question)

README claim: "Every read is bounds-checked, every write goes through `openat` +
`O_NOFOLLOW` (no path-traversal or symlink escape), and decompression is bounded against
bombs" (`moria:README.md:15`). This is the same hostile-input class as Limpet's own wq#3984
tar fix. Verdict by source read: **the mechanism is real, centralized, and structurally
stronger than what Limpet patched in wq#3984 — but upstream has zero tests for it** (§2.3),
so Limpet must gate adoption on its own hostile-sample bench (§5).

### 2.1 Path traversal and symlink escape — verified real

All write traffic routes through one module, `SafeRoot`
(`moria:src/extract/safepath.hpp:1-7`: "This is the tar-extraction hardening, done once"):

- **Component sanitization**: `split()` walks each `/`-separated component, collapses empty
  and `.`, **rejects `..` and NUL outright**, and absolute paths lose their leading `/` by
  the empty-component collapse — `"/etc/passwd"` becomes `etc/passwd` under the root
  (`moria:src/extract/safepath.cpp:29-50`).
- **fd-walk, not string-walk**: parents are opened step-by-step from the root fd with
  `openat(..., O_RDONLY|O_DIRECTORY|O_NOFOLLOW)`, so a previously planted symlink in a
  directory position fails with ELOOP instead of being traversed
  (`moria:src/extract/safepath.cpp:54-57`); the leaf write is `openat(...,
  O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW)` (`moria:src/extract/safepath.cpp:79`).
- **Symlinks stored verbatim, never followed**: `make_symlink` records the target as data,
  NUL-checked, and unlinks any pre-existing leaf so a planted entry can't be written through
  later (`moria:src/extract/safepath.cpp:109-121`).

This is exactly the attack from ISSUE-LOG row 1337 / wq#3984 — a symlink member followed by
a file member that writes *through* it, which sailed past Limpet's lexical `member.name`
check and was caught only by Python 3.14's implicit tarfile filter
(`reports/wq3989_verify_wq3984_limpet_tar_symlink_chain.md:13-22`). moria defends at the
syscall layer, where the race-free truth lives, rather than pre-checking names lexically.
The archive extractors genuinely route through it: tar (`moria:src/extract/tar.cpp:95,165-173`),
cpio (`moria:src/extract/cpio.cpp:63,120`), and the filesystem extractors take a `SafeRoot&`
in signature. One deliberate bypass exists and is benign by construction:
`android_sparse.cpp` writes a reassembled `unsparsed.img` with a **fixed filename** under a
SafeRoot-created subdir, size-gated by `MAX_IMAGE`/`MAX_CHUNKS` before the fd is opened
(`moria:src/extract/android_sparse.cpp:73-84`) — no attacker-controlled path component.

### 2.2 Decompression bombs and runaway recursion — verified real

- Every decompressor takes an explicit output cap: "Decompress `src` into a fresh buffer of
  at most `max_out` bytes … Never throws, never writes past max_out"
  (`moria:src/extract/decompress.hpp:36-41`; streaming variants stop at `cap`,
  `:93-101`).
- Run-level guards live in one struct: "Extraction is on hostile input, so runaway nesting
  and decompression bombs are bounded: a max depth, an absolute ceiling on total files and
  bytes produced across the whole run, and a per-extraction decompression-ratio cap …
  the run still returns what it recovered (never fails out)"
  (`moria:src/main.cpp:150-163`). Defaults: depth 8 (`moria:src/main.cpp:131`), 500 000
  files, 4 GiB total (`moria:src/main.cpp:438-439`); ratio cap output/input > 1000 on any
  output above 10 MB stops descent into that branch and names the offending entry in the
  manifest (`moria:src/main.cpp:197-198,354-362`). Per-format budgets exist too (squashfs
  carries its own anti-bomb `byte_budget`, `moria:src/extract/squashfs.cpp:68`).
- A tripped guard marks the manifest `capped` rather than aborting — degraded output is
  flagged, not silent (`moria:src/main.cpp:155-157`).

Residual exposure is the budget itself: a hostile image can still legally cost 4 GiB of disk
and 500k inodes per run. For a Limpet plugin, pass tighter `--max-bytes/--max-files` from
workspace policy rather than relying on upstream defaults.

### 2.3 The gap the README doesn't mention — no upstream hostile-input tests

The test suite is large and correctness-serious (mount-comparison round-trips), but **greps
for traversal/`..`/hostile/evil samples across `moria:tests/` return nothing, the single
C++ unit file has no SafeRoot test, and the four fuzz targets (`fuzz_scan`, `fuzz_lzx`,
`fuzz_esp32_nvs`, `fuzz_esp32_part`) fuzz parsers, not the extraction write path.** The
safety claim is design-true today, verified above by read — but nothing in upstream CI
prevents a future refactor from quietly adding a write that skips `SafeRoot`
(android_sparse already shows the pattern of a sanctioned bypass). Consequences for Limpet:
(a) the §5 bench's crafted hostile samples are mandatory before any tier promotion, and
(b) pin the adopted commit and re-run the bench on every bump — the hash pin is the only
regression gate until upstream grows its own.

## 3. mithril in one paragraph — and why it waits

mithril scans *content* of an unpacked tree: deterministic offline secrets ladder, SBOM
recovered from package DBs *and* ELF version banners / libc filenames / kernel banner
(works on stripped images with no package manager, `mithril:README.md:19`), CVE join against
a local OSV+NVD/CPE mirror with KEV/EPSS annotation and a curated kconfig-gated kernel-CVE
checklist, SPDX license inventory (`mithril:README.md:80-84`). Scan-time network: none;
the mirror refreshes out of band via `--update-db` (`mithril:README.md:7`). It has no
build-time dependency on moria. **Fit call: track, don't wrap yet.** Its lane is already
assigned — the EMBA wrap (wq#4005 §2) is the planned automated secrets/SBOM/CVE pass, with
richer modules and an aggregation pipeline. mithril's edge over EMBA is being a single
offline no-root binary (EMBA needs root + Docker + network for CVE pulls), which matters
for air-gapped or unprivileged Limpet deployments. Revisit after the EMBA plugin lands: if
EMBA's `escalates=True/network=True` footprint proves operationally heavy, mithril is the
lightweight substitution with `escalates=False, network=False` — the all-default
`MutationBoundary` (`limpet/manifest.py:88-89`).

## 4. Plugin fit — adopt moria as the supplementary extractor, tier `vetted`, not core

**wq#4000's hold-the-line stands: unblob/binwalk remain the extractors of record**
(`design/wq4000_rea_mytechnotalent_dissection.md:208-212`; they are core in the
architecture, `design/wq3806_limpet_architecture.md:98`). moria does not displace them on
six weeks of life; it earns a seat *beside* them for what they are structurally bad at:

1. **The no-root/no-zoo niche.** Where Limpet runs unprivileged or can't carry the external
   extractor zoo (field laptop, CI, air-gap), moria is one static binary covering the §1
   format list in-process. That deployment envelope is Limpet's own (the REA-style
   "runs locally, never escalates" boundary, `limpet/manifest.py:33`).
2. **Manifest / trust boundary.** `PluginManifest` (`limpet/manifest.py:94`) for `moria`
   with `MutationBoundary` **`writes=("workspace",), escalates=False, network=False`**
   (`limpet/manifest.py:78-89`) — the first *extractor* in the registry whose honest
   declaration is privilege-free. A `HealthCheck` (`limpet/manifest.py:68`) probes the
   pinned binary's presence and `--help`; absent → `limitations` note, not silent failure.
3. **Run + ingest.** Process-boundary invocation `moria -j -e <target> -C
   <workspace>/extracted --max-bytes/--max-files <policy>`; `manifest.json` (offset → path →
   type/confidence) maps into findings JSON under `<workspace>/evidence/findings/*.json`
   (`limpet/workspace_report.py:19`), masked on ingest via `mask_text`
   (`limpet/workspace_report.py:82`) — moria extracts key/cert files, so ingest records
   *located*, never re-leaks.
4. **Tier and gate.** Enter at `community`, promote to `vetted` **only after the §5 bench
   passes**, and to core only via the normal `TOOL_REGISTRY.md` go/no-go after real corpus
   mileage. Pin `a39d1386`; every version bump re-runs the bench (§2.3).
5. **The wq#3984 lesson, pointed inward.** Independent of adoption, `SafeRoot` is the
   reference design Limpet's *own* future extraction code should copy: fd-walk +
   `O_NOFOLLOW` beats lexical pre-checks (which is what wq#3984 had to patch, and the patch
   still leans on `tarfile`'s data filter).

**Maintenance risk: medium, priced in.** Repo created 2026-09-07 (5-6 weeks old), one
author (Matt Brown), v0.3.0 — but already merging external PRs (pinned HEAD is the merge of
PR #51 from a second contributor), with packaging, man pages, tests, and fuzzing beyond
hobby grade. MIT means a dead upstream is forkable at zero license cost; the pinned-commit +
re-bench discipline (§2.3) is the same hedge either way. The risk is real but it gates the
*tier* (never auto-promote), not the *adoption*.

## 5. Follow-up bench — the empirical legs this lane couldn't run (filed as wq#4053)

Runnable on any lane with a build-capable allowlist (`cmake`, `g++`, `make`) or a
fleet-exec grant; Lumen-only per the ticket. All steps against the pinned commits.

1. **Build**: `cmake -S moria-live -B build -DCMAKE_BUILD_TYPE=Release && cmake --build
   build -j` (deps: zlib1g-dev, liblzma-dev, liblz4-dev, libzstd-dev).
2. **Coverage/time vs unblob and binwalk**: same inputs from `/home/lumen/re`, each tool to
   a fresh output dir; compare files-recovered, formats-identified, wall time, and
   unresolved-region byte counts. Record per-image, not aggregate.
3. **Hostile samples (must be able to fail, wq#2630)**: (a) tar + cpio with `../../escape`
   members and the wq#3984 symlink-then-write-through-it chain — expect refusal with the
   output tree fully inside the root; (b) a zip/gzip ratio bomb >1000:1 above 10 MB —
   expect `ratio-capped` in the manifest, bounded disk; (c) depth-12 nested containers —
   expect `capped: max-depth` at 8. Samples committed as fixtures so the probe is
   re-runnable evidence.
4. **Promotion decision**: `vetted` iff 3(a-c) all refuse correctly and 2 shows coverage no
   worse than unblob on the fleet's corpus classes.

## 6. Extraction verdict

- **ADOPT (supplementary extractor, MIT GREEN, tier `community`→`vetted` gated on §5) —
  moria** as the no-root, in-process, agent-JSON extractor beside unblob/binwalk (§4);
  unblob/binwalk stay the extractors of record (wq#4000 hold-the-line).
- **VERIFIED WITH A CAVEAT — the safety claims** (§2): traversal/symlink defense and bomb
  bounding are real and centralized (`safepath.cpp`, `RecurCtx`), structurally stronger
  than Limpet's own wq#3984 patch — but upstream ships **no hostile-input tests**, so the
  pin + Limpet-owned bench is the only regression gate.
- **TRACK — mithril** (§3): offline no-root secrets/SBOM/CVE; EMBA (wq#4005) owns that lane
  today; mithril is the lightweight fallback if EMBA's root+Docker+network footprint binds.
- **COPY THE PATTERN — `SafeRoot`** as the reference for any future Limpet-native
  extraction write path: fd-walk + `openat`/`O_NOFOLLOW`, done once, not lexical checks.
- **RISK PRICED — maintenance** (§4): 5-6 week old single-author repos, medium risk, hedged
  by commit pin, re-bench-on-bump, and MIT forkability; gates tier, not adoption.
