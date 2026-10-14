# wq#3879 — FwAnalyzer for LIMPET automated firmware policy checks

**Verdict: adopt as a `vetted` plugin, scoped to rootfs policy gating. Never use its
`cpiofs` backend on untrusted images.** It is a small, declarative, CI-shaped
checker that fits LIMPET's evidence model well. It does not do what the ticket
premise suggests for open ports or debug interfaces (see §1).

Source evaluated: `github.com/cruise-automation/fwanalyzer` at rev `a0db4505`
(2023-10-08, the latest commit; the project has been dormant since then). Every
claim below cites that tree. The binary was **not run**: `go build` is not on the
worker allowlist (ISSUE-LOG row filed). The policies in `research/fwanalyzer/`
are tested statically only (§6).

## 1. What it is, and what the ticket assumes

FwAnalyzer reads a **filesystem image** (`extfs`, `squashfs`, `ubifs`, `vfatfs`,
`cpiofs`) or an unpacked directory (`dirfs`). It applies TOML rules to file
metadata and contents and writes a JSON report (`Readme.md:20-28,109-118`). It does
not unpack whole firmware blobs (that is the job of `devices/*/unpack.sh` or our
unblob step), and it does not emulate anything.

| Ticket item | What FwAnalyzer actually does |
|---|---|
| Default passwords | Yes, but only as regexes over `/etc/passwd`/`/etc/shadow` (empty or known hashes). It has no credential dictionary. |
| Open ports | **No.** It never runs the target. The best it can do is a static proxy: flag listener binaries (`BadFiles`) or boot scripts that start them (`FileContent` regex). Real port state needs firmware-emulation plus a scan. |
| Debug interfaces | Static proxy only: inittab console-shell lines, a `telnetd` started in rcS. JTAG/UART exposure is a hardware question for the LIMPET HAL (`design/wq3806_limpet_architecture.md` §4). |
| Insecure configs | Yes, this is its strength: SUID/world-write/UID/GID/SELinux/capabilities, file presence, regex, digest, JSON field compare, and script hooks. |

## 2. Policy rule format

The rules are TOML with these sections. Each one is a separate analyzer plugin under `pkg/analyzer/`.

- `[GlobalConfig]`: backend selection (`FsType`, `FsTypeOptions`, `DigestImage`).
- `[GlobalFileChecks]`: whole-filesystem flags (`Suid`+allow-list, `WorldWrite`,
  `Uids`/`Gids`, `SELinuxLabel`, `BadFiles` with `*`/`**` globs).
- `[FileStatCheck."/path"]`, `[FilePathOwner."/dir"]`: per-path metadata.
- `[FileContent."name"]`: exactly one of `RegEx` | `Digest` | `Script` | `Json`
  per rule (`filecontent.go:58-67`), with `InformationalOnly` to downgrade a hit.
- `[DataExtract."name"]`: pulls values (regex group, script output, JSON) into
  the report's `data` map.
- `[FileTreeCheck]`: diffs the full tree against a saved `oldtree.json`.
- `[Include."x.toml"]`: composition, resolved via `-cfgpath`.

Traps found in the source, each encoded in our policies:

1. **`Match = true` means "offender when it matches"** (`filecontent.go:178,187`).
   The README example (`Readme.md:291-294`) reads like the opposite.
2. **A configured file that is missing counts as an offender** (`filecontent.go:109-113`),
   and `InformationalOnly` does not suppress that. A single "generic" policy
   therefore floods on every image lacking `/etc/inittab`, `/etc/shadow`, and so on.
   The answer is target-class layering: `limpet_base.toml` contains only checks safe
   on any Linux rootfs, and `busybox_init.toml` includes it and adds init-specific
   rules.
3. `DataExtract` failures go into `data` as an `ERROR:` string, never into offenders
   (`dataextract.go:115-201`). A symlinked `/etc/os-release`, which is common, yields
   `DataExtract ERROR: file is Link`. Links are refused by every check except
   `FileStatCheck` (`Readme.md:198-202`).
4. Line-by-line regex offenders **echo the matching line** (`filecontent.go:182`).
   A rule over `/etc/shadow` would copy password hashes into the report. The report
   belongs in `evidence/`, and only a masked summary should reach agent context (§3).

## 3. Integration with the workspace evidence chain

This maps onto `design/wq3806_limpet_architecture.md` §2.2 and §5:

- **Invocation.** `fwanalyzer -cfg <policy> -cfgpath <policy-dir> -in target/<fs> -out evidence/fwanalyzer/<policy>-<ts>.json`.
  The input is passed by path argument, which satisfies the untrusted-input rule.
- **Manifest entry.** Record the fwanalyzer rev, the sha256 of each policy file
  (including resolved includes), and the input sha256. `image_digest` covers image
  backends only. For `dirfs`, hash the unblob output tree instead.
- **Agent-facing result.** The report is already JSON keyed by path
  (`offenders`/`informational`/`data`), so the meta-server summary is mechanical:
  counts per section, the top N offender paths, and the `data` map, passed through
  the fleet masker. Raw lines stay in `evidence/`.
- **Target identity.** `DataExtract` values (OS release, build IDs) feed
  `manifest.json` target identity.
- **Version diffing.** Commit `FileTreeCheck`'s `current_file_tree_path` output to
  the workspace. The next firmware version of the same target then diffs against
  it, giving new, deleted, and modified files as `informational`. This complements REA
  `compare_application_versions` at the filesystem layer.
- **Script hooks** (`FileContent.Script`, `DataExtract.Script`) bridge to other
  LIMPET tools. Upstream ships `scripts/check_sec.sh` (a checksec wrapper) and cert
  and private-key checkers. Scripts run on the host with the extracted file, so
  they run inside the workspace container like any other tool.

Proposed registry manifest. It is not added to `limpet/registry/tools.d/` because
the tool is not installed on any LIMPET node yet:

```yaml
name: fwanalyzer
path: /usr/local/bin/fwanalyzer
version: "a0db4505"
description: Declarative security-policy checks over firmware filesystem images (TOML rules, JSON report).
category: firmware
capabilities: [firmware-policy-check, filesystem-metadata-audit, filetree-diff]
input_formats: [squashfs, extfs, ubifs, vfat, extracted-filesystem]
output_formats: [json-report]
architectures: [any]
hardware_requirements: {}
health_check: /usr/local/bin/fwanalyzer -h
```

A bare invocation prints usage and exits 1 (`fwanalyzer.go:101-104`). `-h` is
handled inside Go's `flag.Parse` (`fwanalyzer.go:99`), and its exit status has to
be confirmed on the first build. A stronger health check runs the repo's own
`test/squashfs.img` against a one-rule policy.

## 4. CI/CD potential

The shape is good. It is one static Go binary plus backend helpers, a declarative
policy, JSON out, and `-ee` to fail on offenders (`fwanalyzer.go:152-153`).
`devices/check.py` already implements the unpack, analyze, and report pipeline that a
LIMPET step would wrap. Upstream ships a `Dockerfile` that fits the no-network
workspace container.

Caveats for a gate:

- **Exit code 1 is overloaded.** It means usage error, config error
  (`fwanalyzer.go:104,110,123`), and "offenders present" alike. A gate has to parse
  the report: report present means offenders, report absent means tool failure.
  Otherwise a broken policy reads as a security finding.
- **Backend helpers are external binaries**, found via PATH:
  - squashfs: `unsquashfs` (argv exec, `squashfsparser.go:222,291`)
  - ext: **patched** e2tools (`e2ls`/`e2cp` from `crmulliner/e2tools@link_support`, `Readme.md:194-196`)
  - ubifs: `ubireader_*`
  - vfat: `mtools`

  Lumen has `unsquashfs` and `mtools`. It has no `e2ls`, so `extfs` would need the
  fork built.
- **`dirfs` loses ownership.** An unblob extraction run as a non-root user owns
  every file, so UID/GID checks on `dirfs` are meaningless. SUID survival depends
  on the extractor. Metadata checks should run on the native image backend
  (`squashfs` for most router firmware: carve the squashfs blob with unblob, then
  point fwanalyzer at it). Use `dirfs` for content-only policies.

## 5. Hazard: `cpiofs` backend shell injection (code-read, not exploited)

`CpioParser.CopyFile` runs
`sh -c "cpio -i --to-stdout " + filepath[1:] + " < " + imagepath + " > " + dst`
with no quoting (`pkg/cpioparser/cpioparser.go:272`). `filepath` is a member name
read **from the image**. Directory-scoped `FileContent` rules call `FileGet` on
every walked file (`filecontent.go:165`, `analyzer.go:244-248`). A crafted cpio
(initramfs) member such as `x;cmd` would therefore execute `cmd` on the analysis
host. Firmware is untrusted input by definition. LIMPET rules:

- Never set `FsType = "cpiofs"`. Unpack initramfs with unblob and use `dirfs`.
- Run fwanalyzer only inside the workspace container (no network, workspace-only
  bind mount), which architecture §5 already mandates.
- If LIMPET forks the tool (it is dormant upstream anyway), replace the `sh -c` with
  argv `exec.Command("cpio", "-i", "--to-stdout", name)` and stdin/stdout redirection
  in Go.

The squashfs, ext, ubifs, and vfat parsers use argv exec and do not share this flaw
(`squashfsparser.go:222`, `extparser.go:136,181`, `ubifsparser.go:87,135`,
`vfatparser.go:109,165`).

## 6. Deliverables in this ticket

- `research/fwanalyzer/limpet_base.toml`: a baseline that is safe on any Linux
  rootfs. It flags SUID outside busybox, world-writable files, baked-in SSH and
  dropbear host keys, telnetd binaries, empty-password accounts, and uid-0 accounts
  (informational), and extracts OS identity.
- `research/fwanalyzer/busybox_init.toml`: the BusyBox-init target class. It flags a
  console that respawns a shell without login, and telnetd started from rcS.
- `research/fwanalyzer/test_policies.py`: 13 static tests covering TOML parse,
  one method per rule, includes that resolve, and every regex firing on a bad
  line and staying quiet on a good one. Writing the first version caught a real gap:
  the console rule missed `/bin/ash`, BusyBox's default shell.

What the tests do **not** cover: FwAnalyzer's own evaluation of these files. The
`WorldWrite` and `BadFiles` behavior on real images (sticky `/tmp` dirs, busybox
applet symlinks) still needs calibrating on a first run.

## 7. Next steps (follow-up tickets)

1. Build fwanalyzer on LIMPET, pinned at `a0db4505`, inside the workspace container
   image. Run `go test ./...` and `test/test.py` from the repo. Then run both
   policies against a real router image (squashfs) and calibrate the false
   positives from §6.
2. Write the meta-server wrapper: report-present/absent exit disambiguation (§4),
   a masked summary, evidence and manifest entries (§3).
3. Patch the cpio injection in a LIMPET fork, or refuse `cpiofs` in the wrapper (§5).
4. For the parts of the ticket FwAnalyzer cannot cover: credential dictionaries
   and runtime open ports belong to EMBA (not evaluated here) or firmware-emulation
   plus a scan.
