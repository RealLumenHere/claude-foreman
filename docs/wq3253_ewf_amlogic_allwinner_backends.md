# wq#3253 — ewf next backends: Amlogic (pyamlboot) + Allwinner (sunxi-fel) protocol surface

Deliverable path is `/home/lumen/scratch/research/ewf_amlogic_allwinner_backends.md`
(ticket-specified, fleet-shared research tree — not a git repo at all, `git -C
/home/lumen/scratch rev-parse --is-inside-work-tree` fails), so the acceptance-gate
citation below points at this report rather than the scratch file directly — same
cross-repo/no-repo citation gap and workaround as ISSUE-LOG #192 (wq#1440,
`docs/wq2555_index_research_final_upsert_guard.md`). Full content is identical to the
scratch deliverable; this copy exists so the branch has a citable, diffed artifact.

## What shipped

Research-only deliverable, no device commands run. Full text below; acceptance
citation: **AC1 → this file, `## 1.2 pyamlboot's actual command surface` table
(lines 46-60)**, which answers question (1) of the ticket (pyamlboot vs. Amlogic
USB Burning Tool protocol) with the verified opcode table from pyamlboot's
`PROTOCOL.md`, cross-checked against Blasty's independent haxx.in writeup.

---

# ewf next backends: Amlogic (pyamlboot) + Allwinner (sunxi-fel) protocol surface

_2026-10-01 · wq#3253 · web research only, no device commands run · context: tiny_whale_ewaste_recovery_expansion_2026-09-30.md §2 — Amlogic and Allwinner are the next two SoC families by e-waste volume after Rockchip (ewf v1)._

## TL;DR

1. **Both families use the same two-layer shape ewf already knows from Rockchip**: a BootROM-resident low-level protocol (read/write SRAM/DRAM, load+execute code — no native eMMC/NAND verbs) that exists purely to chainload a bootloader, plus a *second*, higher-level protocol spoken by that bootloader which is where real flash I/O happens. pyamlboot and sunxi-fel each only reimplement the **first** layer.
2. **Full-dump/backup is feasible on both families today**, but not via the low-level protocol directly — it works by using pyamlboot/sunxi-fel to chainload a U-Boot build that exposes the eMMC as a USB Mass Storage (UMS) block device, then `dd`-ing it from the host. This is proven and documented for Amlogic GXL/GXM/GXBB (S905/S905X/S905W/S912) and for any Allwinner SoC with decent mainline U-Boot support (H3, H616 solid; A133, A523 partial/experimental).
3. **The real gap is the vendor's own high-level flashing protocol**: Amlogic's "USB Burning Tool" bulk-command protocol and Allwinner's FES (used by PhoenixSuit/LiveSuit for partition-table-aware, CRC-verified eMMC/UFS/SPI-NAND writes) have **no mature open-source reimplementation**. The one project claiming FEL+FES support (OpenixSuit) is closed-source and its own README discloses it was built by handing an AI agent a single giant prompt — treat it as a corroborating data point for protocol shape (VID/PID, chunking, FES verb names), not as a dependency or authority.
4. Recommendation: ewf's Amlogic/Allwinner backends should **not** try to reimplement the vendor high-level protocols. Build on the "chainload a UMS-capable bootloader, then dd" path for backup+restore (mirrors the Rockchip Maskrom+loader backup-before-flash gate already in ewf), and treat partition-table-aware flashing as a stretch goal gated on the specific board having a known-good mainline U-Boot.

---

## 1. Amlogic: pyamlboot vs. Amlogic USB Burning Tool

### 1.1 Two protocols sharing one USB transport

| Layer | Who implements it | What it can do |
|---|---|---|
| **BootROM USB-boot protocol** ("USB recovery protocol") | pyamlboot (OSS, `superna9999/pyamlboot`, 140★); also reimplemented in Amlogic's own closed `aml-flash-tool` and in vendor U-Boot | Vendor-specific USB control-transfer opcodes to read/write SRAM/DRAM and kick off code execution. No partition/filesystem concept at all. |
| **Vendor U-Boot "update"/"usbburning" mode** (entered via U-Boot's `update` command — "v2 usbburning mode" — or by booting straight to it) | Closed: Amlogic USB Burning Tool (Windows), Amlogic's Linux CLI burn tool | ASCII bulk-command protocol (`AM_REQ_BULKCMD`/`AM_REQ_TPL_CMD` etc.) that understands partitions, `getpartition`, `disk_initial`, image-package (`aml_upgrade_package.img`) writes — this is the actual "burn an Android image" protocol. |

Both layers are reached over the **same USB VID:PID, `1b8e:c003`** ("GX-CHIP" on GXL-family descriptors) — BootROM-fresh and post-U-Boot-`update` devices are not distinguishable by VID/PID alone; disambiguation needs a protocol probe (e.g. the `AM_REQ_IDENTIFY_HOST` request, or simply whether bulk-command requests succeed).
Source: boredpentester.com A113X writeup (U-Boot menu dump showing `update - Enter v2 usbburning mode`, `usb_burn-`, `usb_update-`; explicit statement "pyamlboot... also implements this protocol" against the same `1b8e:c003` ID); pyamlboot `PROTOCOL.md` USB descriptor dump.

### 1.2 pyamlboot's actual command surface (BootROM layer only)

From `PROTOCOL.md` (confirmed independently by Blasty's haxx.in A113X bootrom-dumping writeup, which cites the identical opcode table):

| Opcode | Name | Function |
|---|---|---|
| 0x01 | `AM_REQ_WRITE_MEM` | Write ≤64B to SRAM/DRAM (address in wValue:wIndex) |
| 0x02 | `AM_REQ_READ_MEM` | Read ≤64B from SRAM/DRAM |
| 0x03 | `AM_REQ_FILL_MEM` | Fill memory with repeated 4B value |
| 0x04 | `AM_REQ_MODIFY_MEM` | Read-modify-write with mask (7 sub-ops incl. mem-to-mem copy) |
| 0x05 | `AM_REQ_RUN_IN_ADDR` | Jump to address (BL2 decrypt+verify+execute on signed devices) |
| 0x06 | `AM_REQ_WRITE_AUX` / 0x07 `AM_REQ_READ_AUX` | Peek/poke restricted MMIO ("TODO: how does it work" — undocumented even upstream) |
| 0x11/0x12 | `AM_REQ_WR_LARGE_MEM` / `AM_REQ_RD_LARGE_MEM` | Bulk-transfer large buffers (control-OUT setup + bulk EP 0x02/0x03 data phase) — this is what moves BL2/DDR-init/TPL/u-boot images |
| 0x20 | `AM_REQ_IDENTIFY_HOST` | Host identification / probe |
| 0x30/0x31/0x32/0x33/0x34 | `AM_REQ_TPL_CMD`/`TPL_STAT`/`DOWNLOAD`/`UPLOAD`/`BULKCMD` | TPL(=U-Boot)-stage command channel — the `AMLC`/`AMLS`-framed staged-loader handshake (checksum'd 16KB/48KB/64KB chunks with `OKAY` acks) used to push the signed BL2→DDR-init→TPL chain |

None of these opcodes read or write eMMC/NAND/SPI-NOR storage directly — they only move code/data into SRAM/DRAM and execute it. "ChipID" (used for SoC identification) is itself just `AM_REQ_READ_MEM` at a fixed address (`0xc8013c24`), not a distinct command.

### 1.3 Full-dump feasibility

**Proven, per-board, GXL/GXM/GXBB family (S905, S905X, S905W, S905D, S912)** — `boot.py` in pyamlboot. The repo ships board-specific run scripts (e.g. Libre Computer AML-S905X-CC) with two relevant actions:
- `ums-emmc` — loads BL2 (DDR init) + a TPL/U-Boot build configured to expose the eMMC **user hardware partition as a USB mass-storage device** ~5s after boot. Host then uses `dd`/`gnome-disk-imager` for a byte-for-byte read (backup) or write (restore/flash). Documented peak throughput ~10MB/s (limited by the SoC's USB PIO transfer mode, not UMS itself).
- `erase-emmc` — same chain, erase instead of expose.

This is a genuine full-dump/full-restore path and is the closest open equivalent to what the vendor tool does, achieved without touching the closed bulk-command protocol at all.
Source: Libre Computer forum, "AML-S905X-CC eMMC Flashing Steps from Linux with pyamlboot" (hub.libre.computer/t/33) — includes real `run.sh` transcripts for both actions.

**Restricted, G12A/G12B/SM1 family (S905X2, S905X3, S905Y2)** — `boot-g12.py` uses a *different* sub-protocol and, per pyamlboot's own README, **"doesn't permit loading Linux & a ROOTFS from memory"** — it can only push a `u-boot.bin` and must then hand off to Ethernet or USB Fastboot for anything further. A UMS-based backup is therefore not turnkey the way it is on GXL: it requires a per-board mainline/vendor U-Boot build with `CONFIG_CMD_USB_MASS_STORAGE` enabled, chainloaded via `boot-g12.py`, then triggered via Fastboot/serial — same end capability, more assembly required.

**Unverified this session, S905X4/SC2 and newer**: no pyamlboot board-files or community writeups surfaced in this search pass. Flag as **needs device-specific verification** before ewf claims support — do not assume GXL-style turnkey UMS exists.

### 1.4 Secure boot caveat

On production/locked devices the BootROM will only execute a signed BL2 (`AM_REQ_RUN_IN_ADDR` does decrypt+verify in place). TV-box-class e-waste is overwhelmingly **engineering/unlocked** (this is *why* the Armbian/CoreELEC/LibreELEC community can flash them at all via pyamlboot) but ewf should treat "BL2 load rejected / verification fails" as an expected, named failure mode (locked device), not a bug.

---

## 2. Allwinner: sunxi-fel capabilities on H3/H313/H616/A133/A523

### 2.1 sunxi-fel's full command surface (verified from linux-sunxi.org FEL/USBBoot + `sunxi-tools` README)

```
spl file                      Load+execute U-Boot SPL (also transfers main u-boot if embedded)
uboot file-with-spl           Like spl, but starts U-Boot (combinable with further "write")
hex[dump] addr length         Dump memory region in hex
dump addr length               Binary memory dump
exe[cute] addr                 Call function at address
reset64 addr                   RMR request for AArch64 warm boot
memmove dest src size           Copy bytes within device memory
readl / writel addr [value]     Read/write 32-bit value
read addr length file            Memory -> file
write addr file                   File -> memory (+ -with-progress/-gauge/-xgauge variants)
multi[write] # addr file ...       Multiple file transfers sharing one progress bar
ver[sion]                           Show BROM version + SoC ID (e.g. "soc=00162500(A13)")
sid                                  Retrieve 128-bit SID key
clear / fill addr length [v]          Memory clear/fill
spiflash-info                          SPI (NOR) flash info
spiflash-read/-write                    SPI flash <-> file
```

**There is no `mmc`, `nand`, or `fes` subcommand.** Like pyamlboot, sunxi-fel operates purely at the BROM-FEL level: SRAM/DRAM read-write-execute, plus (uniquely vs. Amlogic) direct SPI-NOR flash read/write. eMMC/raw-NAND I/O is out of scope for the tool entirely.

### 2.2 SoC coverage for the five target chips

Confirmed directly from `sunxi-tools/soc_info.c` (current `master`):

| SoC | `soc_id` | Entry present? | Notes |
|---|---|---|---|
| H3 | — | Yes (long-standing) | Mature, widely used (Orange Pi etc.) |
| H313 | — | **No separate entry** | H313 is a cost-reduced/rebadged H616; devices commonly enumerate/report as H616 (same confusion the e-waste context doc already flags for X96Q-class clones) |
| H616 | 0x1823 | Yes | `sram_swap_buffers`/thunk tables present — basic FEL + SPL support confirmed |
| A133 | — | Yes (table entry present) | **But**: `sunxi-tools` issue #207 (2024, still open-state findings as of this search) reports a custom A133 eMMC board resetting mid-flash via sunxi-fel, and that mainline U-Boot lacks full A100/A133 support — treat as **partial/fragile**, not production-ready |
| A523 | 0x1890 | Yes | Very recently landed (`fel: add Allwinner A523 SoC support`) — no maturity signal found this session; treat as **experimental** |

So all 5 named SoCs are at least FEL-detectable and support the generic read/write/exe primitives; only H3 and H616 have strong field evidence of working end-to-end (FEL → mainline U-Boot → boot/flash). A133 and A523 need a per-device smoke test before ewf claims support, not just a soc_info.c table entry.

### 2.3 FES / LiveSuit: the actual OSS gap

FES is Allwinner's own second-stage protocol (spoken by a FES-mode-capable bootloader, analogous to Amlogic's bulk-command layer) that PhoenixSuit/LiveSuit use for partition-aware, checksum-verified full-image flashing to eMMC/UFS/SPI-NAND. **No open-source tool in the sunxi ecosystem implements FES.** The only project claiming it (`YuzukiTsuru/OpenixSuit`) is:
- Closed-source ("source code is not publicly available (yet)" per its own README — only prebuilt releases are published);
- Self-described as built by handing a single enormous prompt to an AI coding agent (`claude --dangerously-skip-permissions ...`) in one shot — its README literally includes that prompt verbatim. This is a useful corroborating data point (it names the FEL→FES handoff, the `1f3a:efe8` VID:PID, 64KB FES chunking, `fes_query_storage`/`fes_down`/`fes_verify_value` verb names, and CRC32 verification) but should not be treated as a vetted protocol spec or as something ewf should depend on or vendor.

Net: FES reimplementation is a real, acknowledged gap in OSS, consistent with what the ticket asked to confirm. ewf should not attempt to close it; use the UMS-chainload path instead (§2.4).

### 2.4 Backup-before-flash path (mirrors Amlogic's shape)

Standard community method, confirmed via linux-sunxi.org:
1. `sunxi-fel uboot <board>-u-boot-sunxi-with-spl.bin` — chainload mainline U-Boot (needs a build with `CONFIG_CMD_USB_MASS_STORAGE` for the target board).
2. Either push a `boot.scr`/uEnv override via `sunxi-fel write` that auto-runs `ums 0 mmc 0`, or (if UART is wired) interrupt autoboot and run `ums 0 mmc 0` manually.
3. Host sees a new block device; `dd` it for full backup before any write, exactly like the Amlogic UMS path and ewf's existing Rockchip Maskrom+loader backup gate.

Works wherever mainline U-Boot has solid eMMC+USB-gadget support for the SoC (H3, H616); degrades to "needs per-board verification" on A133/A523 per §2.2.

---

## 3. VID/PID + mode fingerprints

| Family | Mode | VID:PID | Distinguishing signal beyond VID:PID | Source |
|---|---|---|---|---|
| Amlogic | BootROM (Maskrom-equivalent) **and** vendor U-Boot "usbburning"/update mode | `1b8e:c003` ("Amlogic, Inc." / "GX-CHIP") | Same ID for both states; probe via `AM_REQ_IDENTIFY_HOST` (0x20) or whether bulk-command (0x34) requests succeed to tell "raw BootROM, no loader yet" from "loader running" | pyamlboot `PROTOCOL.md`; boredpentester A113X writeup |
| Allwinner | FEL mode (all 5 target SoCs) | `1f3a:efe8` ("Allwinner Technology sunxi SoC OTG connector in FEL/flashing mode") | Identical ID baked into every SoC's BROM; actual chip model comes from `sunxi-fel version` output, e.g. `soc=00162500(A13)`, or the `soc_id` field (H616=0x1823, A523=0x1890) | linux-sunxi.org FEL/USBBoot; multiple lsusb captures (Banana Pi forum, Armbian forum, sigmdel.ca); `soc_info.c` |

Practical implication for ewf's device-detect step: VID:PID alone tells you *which family* (Amlogic vs. Allwinner) and *that* it's in a flashable pre-OS mode, but not the exact SoC or (for Amlogic) the exact sub-mode — both need a protocol-level follow-up query, same shape as Rockchip's Maskrom-vs-Loader distinction ewf v1 already handles.

---

## 4. udev rules

**Amlogic** (`1b8e:c003`), confirmed working rule from `superna9999/aml-usb-load-uboot`:
```
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="1b8e", ATTR{idProduct}=="c003", MODE:="0666", SYMLINK+="worldcup"
```
(the `SYMLINK+=` and its "worldcup" literal are that repo's own convention — ewf should drop the symlink line and keep just the match + MODE).

**Allwinner** (`1f3a:efe8`), confirmed working rule from `YuzukiTsuru/OpenixSuit` packaging docs:
```
ACTION=="add", SUBSYSTEM=="usb", ATTRS{idVendor}=="1f3a", ATTRS{idProduct}=="efe8", MODE="666", GROUP="users"
TEST=="power/autosuspend", ATTR{power/autosuspend}="-1"
```
The second line (disable USB autosuspend for the device) matters in practice — FEL sessions have been reported to drop mid-transfer on autosuspend-enabled kernels.

Both should ship as `/etc/udev/rules.d/`-style files in ewf's install/package step (same pattern as any existing Rockchip `rules.d` entry ewf v1 ships), not left as a manual setup step for the user.

---

## 5. Recommended typed-command surface for the ewf Rust backend

ewf v1 (per the tiny-whale research doc) is already structured as: Tauri app, Rust core, **typed Rust commands over the existing CLI tools**, backup-first gate, one backend per SoC family. The Amlogic and Allwinner backends should follow the same shape rather than reimplement USB protocols from scratch in Rust — shell out to `pyamlboot`/`boot.py`/`boot-g12.py` and `sunxi-fel` (both are the maintained, community-trusted implementations; reimplementing the opcode tables in §1.2/§2.1 buys nothing and adds a maintenance burden chasing upstream fixes).

```rust
enum SocFamily { Rockchip, Amlogic, Allwinner /* , MediaTek later */ }

enum DeviceMode {
    // Amlogic
    AmlogicBootRom,        // 1b8e:c003, no loader yet
    AmlogicLoaderUpdate,    // 1b8e:c003, vendor/community U-Boot "update" mode running
    // Allwinner
    AllwinnerFel,           // 1f3a:efe8
}

struct DeviceFingerprint {
    vid: u16,
    pid: u16,
    family: SocFamily,
    mode: DeviceMode,
    soc_model: Option<String>,   // e.g. "H616", "GXL/S905X" — None until identify_soc() succeeds
}

trait SocBackend {
    /// VID:PID scan + family/mode classification. Cheap, non-destructive, always safe to call.
    fn detect(&self) -> Result<DeviceFingerprint, EwfError>;

    /// Protocol-level probe beyond VID:PID: AM_REQ_IDENTIFY_HOST / ChipID read (Amlogic),
    /// `sunxi-fel version` soc_id (Allwinner). Distinguishes exact chip + sub-mode.
    fn identify_soc(&self, fp: &DeviceFingerprint) -> Result<SocModel, EwfError>;

    /// MANDATORY before any write path (ewf's existing backup-first gate). Chainloads a
    /// UMS-capable loader (board-specific asset) and dd's the exposed block device to
    /// `dest`, hashing as it streams. Returns Unsupported(reason) rather than silently
    /// no-op-ing when the board/SoC combination has no known UMS-capable loader asset
    /// (e.g. G12-family Amlogic without a per-board build, or A133/A523 pending verification).
    fn backup_full(&self, fp: &DeviceFingerprint, dest: &Path, progress: ProgressCb)
        -> Result<BackupManifest, EwfError>;

    /// Boot-only: push a binary and execute it, no persistent write. This is the
    /// non-destructive subset pyamlboot/sunxi-fel already do natively — useful for
    /// "does this image even boot" checks before committing to a flash.
    fn boot_only(&self, fp: &DeviceFingerprint, binary: &Path) -> Result<(), EwfError>;

    /// Gated on a prior successful backup_full() for this device (or an explicit,
    /// logged override flag) per the mandatory-backup rule. Chainloads the same
    /// UMS path and dd's `image` onto the block device; verify=true re-reads and
    /// hashes after write.
    fn flash_image(&self, fp: &DeviceFingerprint, image: &Path, verify: bool)
        -> Result<FlashResult, EwfError>;

    /// Explicit, separately-confirmed destructive op (maps to pyamlboot's erase-emmc /
    /// an equivalent Allwinner UMS-then-blkdiscard). Never implied by flash_image.
    fn erase(&self, fp: &DeviceFingerprint, scope: EraseScope) -> Result<(), EwfError>;
}

enum EwfError {
    Unsupported { soc: String, reason: String },  // e.g. "G12-family: no UMS loader asset for this board"
    SecureBootRejected,                             // BL2/SPL verification failed (Amlogic locked device)
    DeviceNotFound,
    Protocol(String),
}
```

Notes on the design:
- `backup_full`/`flash_image` are implemented identically in spirit for both families (chainload-a-UMS-loader-then-dd), so the two backends share most of the orchestration logic; only the chainload step (`pyamlboot`/`boot-g12.py` invocation vs. `sunxi-fel uboot` + env-override push) differs per family, and only the *loader asset selection* (which board file / which prebuilt U-Boot) differs per SoC within a family.
- `Unsupported` must be a real, surfaced variant — per the fleet's acceptance-probe doctrine, a backend that silently treats an unsupported board the same as a supported one can't fail, which defeats the point of probing it. G12-family Amlogic and A133/A523 Allwinner should start in this state until a board-specific loader asset is verified and added.
- No command here reimplements Amlogic's bulk-command protocol or Allwinner's FES — per §1.3/§2.3/§2.4 that functionality is not needed for full backup/flash/restore, only for the vendor GUI's partition-table-aware convenience features, which are out of scope.

---

## Sources (this session)

pyamlboot README + `PROTOCOL.md` (github.com/superna9999/pyamlboot); boredpentester.com "Retreading The AMLogic A113X TrustZone Exploit Process" (2023-06-30); haxx.in "Dumping the Amlogic A113X Bootrom" (Blasty, 2022-12-01); Libre Computer forum "AML-S905X-CC eMMC Flashing Steps from Linux with pyamlboot" (hub.libre.computer/t/33); `superna9999/aml-usb-load-uboot` (udev rule); linux-sunxi.org `FEL/USBBoot` wiki page; `linux-sunxi/sunxi-tools` README + `soc_info.c` (github.com, master branch, fetched 2026-10-01); `linux-sunxi/sunxi-tools` issue #207 (A133 support); `YuzukiTsuru/OpenixSuit` README (FES verb names, udev rule, VID:PID — closed-source, AI-generated, corroborating only); OpenCentauri "Booting into Allwinner FEL mode" docs + Banana Pi/Armbian forum lsusb captures (1f3a:efe8 confirmation); context doc `tiny_whale_ewaste_recovery_expansion_2026-09-30.md` §2.
