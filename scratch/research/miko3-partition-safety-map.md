# Miko 3 partition & peripheral safety map — wq#4072

Target: MIKO1070XXM3E0408B7. Context: **three Miko 3 units are already bricked** — this
doc and the toolkit at `ops/wq4072-miko3-partition-safety/` exist so no further flash
attempt is a blind write. Nothing here is an instruction to flash; it is the
pre-flash verification gate the ticket asks for.

## 0. What's already known about this hardware (in-repo corpus, prior tickets)

- **Base board MCU — CONFIRMED** (`agents/work-queue/reports/223_miko_mcu_disasm.md`):
  Cortex-M3, 256KB flash / 48KB SRAM, GD32F103VCT6-class (ST-compatible). Runs a UART
  firmware-update bootloader (`0x08000000`-`0x0800EFFF`) + application (`0x0800F000`+).
  **Zero FatFs/SDIO/eMMC code** in this image — the base MCU does not touch the eMMC and
  is not the chip this ticket's ADB/fastboot steps apply to. Its live command protocol
  (`GETFV`, `MTRLP`/`MTRRP`/`MTSTP`/`MTBCK`, `SHTDN`, etc.) is what `uart_identify.py`
  queries non-destructively (`GETFV` only — never a motor/program command).
- **Head SoC — CONTRADICTION, not yet resolved.** `agents/work-queue/reports/223_miko_mcu_disasm.md`
  (2026-07-16, citing `bundle/MANIFEST.md`) names the head daughterboard SoC as
  **MediaTek MT7658MSN** (an IoT Wi-Fi/BT combo chip, RTOS). This ticket instead names
  **MT8167** (a real applications processor — ARM Cortex-A35, Android-capable, with
  fastboot/ADB and eMMC storage — consistent with steps 1/2/3 here needing root ADB and
  fastboot `getvar`). These cannot both be the eMMC-driving head SoC on the same unit.
  Per instinct 8 (fabricated evidence), **do not resolve this by picking one and
  asserting it** — confirm from the device itself: `probe_stock.py run`'s
  `version-bootloader`/`product` fields and the actual USB VID:PID enumerated in BROM
  or fastboot mode are the ground truth, not either report. If the unit enumerates as
  `0e8d:2000` on the bench host, that is MediaTek-family-consistent with MT8167 (see
  §1) and corroborates the ticket's framing over the July MANIFEST read — but it is not
  itself proof of exact chip model (mtkclient narrows that from the DA handshake, not
  the PID — see `research/WQ3923_USB_BOOT_MODE_SIGNATURES.md`).
- **eMMC (Kimtigo KMTQ8A1032CXA)** is the animation-asset store, confirmed driven by
  the head SoC, not the base MCU (`agents/work-queue/reports/224_miko_asset_recovery.md`,
  `agents/work-queue/reports/miko3-emmc-animation-assets-20260709.md`). FAT-vs-raw asset
  layout was undetermined as of wq#224 pending a head-SoC image dump — the partition
  backup this ticket asks for (step 1) is exactly that missing image.

## 1. USB VID:PID identification (step 2/3 precondition)

From the committed fleet signature table (`ops/wq3809-limpet-hal/usb_boot_signatures.toml`,
wq#3923): MediaTek VID `0e8d`, PID `2000` lists `soc_family` including **MT8167**
explicitly, `mode_name = "BROM (mask ROM, modern DA protocol V5/V6)"`, confidence 0.85.

**Open contradiction, flag before trusting the label:** `kb/ISSUE-LOG.md` row 1365
(wq#3933 verify-pass of wq#3923) found that mtkclient's own `usb_ids.py` tags
`0e8d:2000`/`2001` as **Preloader**, not BROM — i.e. the device has already run past
mask-ROM by the time it enumerates at that PID. That verify-pass is logged
`CONFIRMED`/escalated and **not yet fixed in the toml** ("not fixed — out of scope for
a verify-pass, do NOT re-implement here either; that's a separate ticket's fix").
**Practical consequence for this ticket:** if MIKO1070XXM3E0408B7 enumerates as
`0e8d:2000`, treat it as **Preloader-stage**, not raw BROM — mtkclient workflows that
assume a from-BROM handshake (payload/DA push) may not apply the same way. Do not
re-run the wq#3923 fix here; route a correction to that ticket's owner instead.

`probe_stock.py` captures `fastboot getvar all` (post-Preloader, already in the
bootloader's fastboot interface) — this is the normal, expected interface for an
MT8167 Android-class device and is independent of the BROM/Preloader VID:PID question
above, which only matters if the unit needs a BROM-mode recovery path.

## 2. Cross-reference against benwilson/miko3-jailbreak (MT8168) — BLOCKED, not fabricated

Both prior-art search paths are down this run, a pre-existing logged outage, not new:
`fleet-search web` returns "all general web-search engines suspended/blocked (brave,
duckduckgo, google cse, startpage)" with no `BRAVE_API_KEY` fallback (wq#2595, open);
`fleet-search github` returns 401, no usable token. This matches the exact precedent in
`kb/ISSUE-LOG.md` row 1332 (wq#3935), whose resolution was to proceed from the in-repo
research corpus rather than block — done above in §0/§1.

**What this means concretely:** I have not read benwilson/miko3-jailbreak and am not
asserting anything about its contents. MT8168 vs our MT8167 is a one-digit-apart
MediaTek part number — plausible as "closely related reference design, same BROM/DA
generation" per the `0e8d:2000` table entry already covering both MT8167 and MT8173/
MT8183/MT8195 under one PID (PID doesn't discriminate exact model — the table's own
note says so), but **do not assume MT8168 partition layout/offsets transfer to MT8167
without confirming it against this unit's own `fastboot getvar`/partition-table probe
first.** Treat any eventual benwilson docs as a secondary cross-check on a backup
that's already been taken and SHA256-verified — never as the thing that justifies
skipping the backup.

**Follow-up required (not a Frank-gate — a missing capability, same shape as wq#2595):**
mint a GitHub token or Brave API key so a future run of this ticket's lane can actually
fetch benwilson/miko3-jailbreak. Until then this cross-reference step stays open.

## 3. Pre-flash procedure — do not skip or reorder

1. **Backup first, always.** `partition_backup.py manifest`/`backup_all_partitions()`
   dd's every partition to a local file over root ADB, then SHA256-manifests the pulled
   set. `verify_manifest()` must return `[]` (clean) before step 4 below. A manifest
   with any `MISSING`/`MISMATCH`/`UNTRACKED` entry means the backup is not trustworthy
   — do not proceed past it.
2. **Probe, don't infer.** `probe_stock.py run` records every fastboot `getvar`
   param (slot count, partition sizes, `secure`, bootloader version) *as this specific
   unit reports them* — this is what resolves the §0 head-SoC contradiction and
   whether MT8168-sourced offsets (once reachable, §2) actually apply here.
3. FACTFACT META test — MediaTek factory test mode entry/exit. **Not built this run**:
   no confirmed-safe META-mode command sequence for this exact unit was found in the
   in-repo corpus or (search being down) externally; inventing one would be exactly the
   fabricated-evidence failure mode instinct 8 warns about. Leave as an explicit open
   item for whoever has live search or direct hardware access to confirm the command
   sequence before running it, rather than guessing.
4. `i2c_survey.sh` — read-only `i2cdetect -y` sweep of every enumerated `/dev/i2c-*`
   bus. Safe regardless of flash state; run any time.
5. `uart_identify.py` — sends only `GETFV` (confirmed read-only status query, §0) across
   `/dev/ttyS*` × common baud rates to find the base-board motor UART. Never sends
   `MTRLP`/`MTRRP`/`PRGFL`/`SETMR` or any other command from the protocol table.
6. `dmesg_capture.sh` — full kernel ring buffer, read-only (no `-c`).
7. `sdcard_slot_test.sh` — enumeration + read-only `dd if=<device>` probe. Never
   `dd of=<device>` (fleet-wide rule, carried from the eMMC runbook in
   `agents/work-queue/reports/224_miko_asset_recovery.md`: `dd` reads from a device are
   fine, writes to a device are not — Balena Etcher only for USB writes).

Only once 1–7 are clean (manifest verified, FB params recorded, META-mode sequence
confirmed from a real source rather than guessed, buses/UART/SD mapped, dmesg captured)
does a flash attempt have a rollback path. This safety map does not itself authorize a
flash; it is the precondition checklist the ticket asked for.

## Toolkit

`ops/wq4072-miko3-partition-safety/` — `partition_backup.py`, `probe_stock.py`,
`i2c_survey.sh`, `uart_identify.py`, `dmesg_capture.sh`, `sdcard_slot_test.sh`, 19
passing unit tests under `tests/`. See that directory's README for the write-guard
(`partition_backup.guard_command()`) every subprocess call here passes through, and
for why nothing was run against real hardware in this sandbox (no bench-host execution
path — `kb/ISSUE-LOG.md` row 114).
