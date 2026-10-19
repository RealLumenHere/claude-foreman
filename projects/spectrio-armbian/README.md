# spectrio-armbian

Armbian bring-up notes and flash assets for Spectrio-brand digital-signage
players built on the Amlogic S905X3 (SM1) SoC — written up here for
publication as a standalone, community-facing GitHub project
(`spectrio-armbian` or similar; wq#4318).

These boxes are the same hardware backing the fleet's "TF1" appliance
project (`projects/spectrio-tf1/`, `ops/wq3430-spectrio-tf1-deploy/`) — that
project builds Tailscale/Klipper/AdGuard/kiosk appliances *on top of* the
bring-up this repo documents. This repo is the bring-up itself: getting a
mainline-ish Armbian booting and networked on stock hardware the vendor
never meant to run Linux on.

## What's in here

| File | Covers |
|---|---|
| `DEVICE_QUIRKS.md` | SoC/board identity, the Ethernet PHY mismatch and how it was fixed, memory layout |
| `BOOT_ARGS.md` | Kernel command-line / U-Boot environment changes needed to boot reliably |
| `FLASH_PROCEDURE.md` | How to get the image onto removable media and boot it on the device |
| `EMMC_INSTALL.md` | Moving from a USB trial boot to a permanent internal-eMMC install |
| `provision.sh` | The actual overlay script that patches an ophub Armbian image for this board (reproduced verbatim from the working fleet copy) |
| `meson-sm1-spectrio-tf1.dtb` | The working board DTB, byte-identical to the fleet's golden copy (sha256 `0f1f9b3a…2a88ef`) |
| `meson-sm1-spectrio-tf1.dts` | That DTB decompiled. The Ethernet fix is at lines 180-209 (MAC) and 1922-1948 (PHY) |

## Base image

[`ophub/amlogic-s9xxx-armbian`](https://github.com/ophub/amlogic-s9xxx-armbian)
— the community Armbian-for-TV-boxes builder, `s905x3` target. This project
does **not** fork or replace that image; it documents the board-specific
delta needed to run it on Spectrio hardware (custom DTB ethernet section,
boot-arg fix, eMMC install path) that the stock build doesn't know about.

## Status / what's still missing from this writeup

The bring-up artifacts are in the fleet's shared scratch tree on lumen
(`projects/spectrio-tf1/` → `/data/lumen/projects/spectrio-tf1-cluster/`),
not on the RE workstation that earlier notes pointed to (wq#4322 searched it
and found nothing). wq#4326 pulled them from there:

- **Board DTB/DTS:** shipped here byte-for-byte from `golden/`, including
  the full PHY node (`DEVICE_QUIRKS.md`). The real node replaced the earlier
  prose stand-in and corrected it. The node is named `ethernet-phy@0` with
  `reg = <0x05>`, not `ethernet-phy@5`, and it keeps the X96 Max+
  reset/IRQ properties.
- **`mem=3552M` root cause:** written up in `BOOT_ARGS.md` and
  `DEVICE_QUIRKS.md` → "Memory". The vendor U-Boot leaves TrustZone/BL31
  memory at the top of DRAM undeclared, and without the cap the kernel
  takes an SError in `paging_init`.
- **Flash/eMMC procedure:** `FLASH_PROCEDURE.md` and `EMMC_INSTALL.md` now
  follow the fleet's tested procedure: USB only, toothpick first boot, the
  `armbian-install` Customize answers, no `-m yes`. Two corrections came out
  of this: SD cards don't boot this box, and `provision.sh` doesn't add the
  boot args.
- **Per-unit transcripts: none exist.** The scratch tree has census and
  backup *scripts* and one unit's eMMC partition dump. Its non-empty files
  include no flash or install logs, and its own methodology doc lists the
  per-unit SD/eMMC boot proof as the bench step still to do. So this repo
  documents the procedure, not a recorded run of it.

Not published: that eMMC partition dump. It's a full vendor
bootloader/env/recovery image of one physical unit, and per-device
identifiers live in env.

The same applies to the `mem=3552M` boot-arg cap: this repo documents where
it goes (`BOOT_ARGS.md`) but ships nothing that applies it, and carries no
transcript of it being applied and booting. Treat that as a documented
manual step, not an automated or re-verified one.

Before pushing this to a public, Lumen-external GitHub repo: creating a new
*public* repository and the initial push both need a GitHub credential this
worker's `gh` allowlist doesn't carry (only `gh pr/issue/run` read
subcommands are permitted) — queued as a follow-up for whoever holds that
credential, not guessed at or worked around here.
