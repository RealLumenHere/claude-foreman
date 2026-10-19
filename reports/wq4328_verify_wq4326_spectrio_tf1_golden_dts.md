# wq#4328 — verify wq#4326 (PR#2228, land golden TF1 DTS/DTB, real PHY node, mem=3552M root cause)

**Verdict: CONFIRM.** wq#4326's done-notes hold under a fresh adversarial pass. Every
checkable technical claim — the PHY node's name/reg mismatch, the `/memory@0` placeholder,
the `reserved-memory` layout, the `mem=3552M` arithmetic, the `.dts` line citations, and
`provision.sh`'s exact action set — was independently re-derived from the raw `.dtb` bytes
or the committed scripts, not just read back from the PR's own prose. No contradiction
found.

## Scope re-derived, independent of the author's report

Single squash-merge commit `a3acf112` on `main`. `git show --stat a3acf112` confirms 7
files touched: `BOOT_ARGS.md`, `DEVICE_QUIRKS.md`, `EMMC_INSTALL.md`, `FLASH_PROCEDURE.md`,
`README.md` (all modified), `meson-sm1-spectrio-tf1.dtb` (new, binary), and
`meson-sm1-spectrio-tf1.dts` (new, 3936 lines). Deliverable
(`projects/spectrio-armbian/DEVICE_QUIRKS.md`) matches the ticket's `deliverable` field.
`grep -rn "TODO\|FIXME\|XXX" projects/spectrio-armbian/` is empty — the ticket's "replacing
the remaining TODO markers" acceptance item is satisfied.

`dtc`/`fdtget` are still not on the worker allowlist (confirmed: both refused with "This
command requires approval" when I tried them directly, same gap ISSUE-LOG row 1461
recorded for wq#4326's own attempt). Followed wq#4326's own workaround rather than
retrying: wrote a ~75-line stdlib-only FDT struct-block reader
(`/tmp/fworker-wq4328/fdt_check.py`, run via `python3 -I`) and parsed the committed
`.dtb`'s raw bytes myself, independent of trusting the shipped `.dts` text or the PR's
prose.

## Things I tried to break and couldn't

1. **The central claim — `ethernet-phy@0` node keeps its stock name but `reg` moved to
   `0x05`.** This is the single most load-bearing and most "could be fabricated" fact in
   the whole PR (a node named `@0` with `reg=5` is the kind of detail an author could
   invent without anyone noticing). Parsed it straight out of the binary struct block,
   not the `.dts`: `mdio-multiplexer@4c000/mdio@0/ethernet-phy@0` → `reg = 00000005`,
   `phandle = 00000009`, `max-speed = 000003e8`, reset/IRQ properties all present —
   byte-for-byte identical to `DEVICE_QUIRKS.md`'s quoted node and to the committed `.dts`
   at lines 1938-1947. Cross-checked the MAC node's `phy-handle = <0x09>` (dts line 193)
   against the PHY node's own `phandle = <0x09>` (dts line 1946, confirmed `0x09` in raw
   bytes too) — they match, so the MAC-to-PHY link the doc describes is real, not just
   asserted.
2. **README's line-number citation ("lines 1922-1948 (PHY)")** — checked against the
   actual file rather than trusting it: line 1922 is `mdio-multiplexer@4c000 {`, line 1948
   is its closing `};`. Exact.
3. **`/memory@0` "placeholder" and `reserved-memory` layout** (the `mem=3552M` root-cause
   narrative's factual basis) — raw parse: `/memory@0` → `reg = 0x0 0x0 0x0 0xffffffff`
   (literally the documented placeholder); `/reserved-memory/secmon@5000000` → base
   `0x5000000` size `0x300000` (3 MiB); `secmon@5300000` → base `0x5300000` size
   `0x2000000` (32 MiB); `linux,cma` → size `0x10000000` (256 MiB), `reusable`,
   `linux,cma-default`. All four numbers match `DEVICE_QUIRKS.md` → "Memory" exactly,
   including the MiB conversions.
4. **`mem=3552M` ↔ `0xDE000000` arithmetic** — `3552 * 1024 * 1024 = 3724541952 =
   0xDE000000`. Checks. `4096 - 3552 = 544` MiB "given up" — also checks, and is the only
   place a "4 GiB part" assumption is load-bearing; `DEVICE_QUIRKS.md:152` states the 4 GiB
   assumption explicitly, so this isn't a hidden/unstated premise.
5. **`.dtb` sha256** — `sha256sum meson-sm1-spectrio-tf1.dtb` → `0f1f9b3a…2a88ef` (full
   hash, prefix and suffix both match what's quoted in `DEVICE_QUIRKS.md` and `README.md`).
6. **"`.dts` is that same `.dtb` decompiled" claim** — not just asserted-and-trusted:
   since the raw-byte parse (independent of the `.dts` text) reproduces the `.dts`'s PHY
   and memory nodes property-for-property, the two files are provably not
   independently-authored-and-coincidentally-matching.
7. **`provision.sh`'s "three actions, complete set" claim** (`FLASH_PROCEDURE.md` step 3,
   `README.md` Status) — read the actual script: `cp` the `.dtb`, `cp` `u-boot.ext`, one
   `sed` on the `FDT=` line. No `mem=`/`APPEND=` handling anywhere in it. Matches "does not
   touch the kernel command line" and "provision.sh lacks mem=" exactly.
8. **Cross-file consistency on hardware-specific prose that can't be verified against any
   artifact in this sandbox** (vendor U-Boot prompt `sm1_ac214_v1#`, blue USB-A host port,
   toothpick reset, `root`/`1234` login, serial pinout) — at minimum internally consistent
   between `BOOT_ARGS.md` and `FLASH_PROCEDURE.md`; no contradiction between files.
9. **MAC-node RGMII delay claim** (`amlogic,tx-delay-ns = <0x2>`, no `amlogic,rx-delay-ns`,
   no `rx-internal-delay-ps`) — checked against the `.dts` MAC node directly (lines
   180-208): present and absent exactly as described.

## Not re-litigated (correctly out of scope, per the PR's own honesty)

- **Golden-source provenance.** I cannot reach
  `/home/lumen/projects/spectrio-tf1/golden/` or
  `/data/lumen/projects/spectrio-tf1-cluster/golden/` from this worktree (both paths
  blocked — "outside allowed directories," no `needs:dir` grant on this verify ticket, and
  per doctrine the path being refused is not retryable with a synonym command). Everything
  above verifies that the committed `.dtb`/`.dts`/docs are *internally consistent and
  mutually corroborating*; it cannot prove the `.dtb` itself is the real fleet artifact
  rather than a self-consistent fabrication. This is the one gap an adversarial pass from
  this sandbox structurally cannot close. Treating it as the ceiling on this verify's
  confidence, not as grounds to contradict — the PR's own README is explicit about every
  other unverified/unapplied step (no per-unit transcripts, `mem=` cap never applied on
  hardware, eMMC dump unpublished), which is the opposite of the overclaiming pattern a
  contradiction would need.
- **Physical hardware claims** (serial pinout, button/port locations, vendor prompt
  string) — no bench access from this sandbox; flagged as internally consistent only
  (point 8), not independently confirmed.
- **`pyamlboot`/`boot-g12.py` capability claim and the LKML `intel-xway` citation** — both
  are external-world facts (tool capability, upstream patch thread) outside what this
  sandbox can fetch; not re-litigated.

No contradiction found. Not escalating to opus-5@high.
