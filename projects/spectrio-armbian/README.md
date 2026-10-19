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
| `EMMC_INSTALL.md` | Moving from a USB/SD trial boot to a permanent internal-eMMC install |
| `provision.sh` | The actual overlay script that patches an ophub Armbian image for this board (reproduced verbatim from the working fleet copy) |

## Base image

[`ophub/amlogic-s9xxx-armbian`](https://github.com/ophub/amlogic-s9xxx-armbian)
— the community Armbian-for-TV-boxes builder, `s905x3` target. This project
does **not** fork or replace that image; it documents the board-specific
delta needed to run it on Spectrio hardware (custom DTB ethernet section,
boot-arg fix, eMMC install path) that the stock build doesn't know about.

## Status / what's still missing from this writeup

The RE session that produced this bring-up used the fleet's dedicated RE
workstation (internal hostname "maksoor") for the physical board work, but
the primary artifacts this writeup still needs — the full board `.dtb`/
`.dts`, the exact per-unit flash/install transcripts, and photos of the
board/PHY — are not there. wq#4322 (follow-up to wq#4318) got a working
`fleet-exec run maksoor` grant (confirming ISSUE-LOG #192/wq#1444's fix
holds) and exhaustively searched maksoor's home tree for them: nothing
matching `golden`, `spectrio-tf1`, or `meson-sm1-spectrio-tf1` exists there
— only generic, board-agnostic ophub device-tree-overlay blobs.

The actual source this writeup's technical content was reconstructed from
(`projects/spectrio-tf1/golden/` and
`projects/spectrio-tf1/census/CENSUS_METHODOLOGY.md`, cited inline in
`DEVICE_QUIRKS.md`) turns out to be a fleet-shared, non-git scratch tree
**local to the Lumen host itself** — confirmed real via symlink resolution
to `/data/lumen/projects/spectrio-tf1-cluster/golden` — not on maksoor.
Pulling the complete artifacts needs a `needs:dir(/data/lumen/projects/spectrio-tf1-cluster)`
grant (not an ssh grant) on whatever worker does the pull; a `needs:ssh(maksoor)`
grant, however correctly minted, cannot reach this path. Follow-up ticket
wq#4326 is filed with the right tag; see `kb/ISSUE-LOG.md` row 1457 for the
full diagnosis of how `ssh(maksoor)` ended up on wq#4322 instead.

Before pushing this to a public, Lumen-external GitHub repo: creating a new
*public* repository and the initial push both need a GitHub credential this
worker's `gh` allowlist doesn't carry (only `gh pr/issue/run` read
subcommands are permitted) — queued as a follow-up for whoever holds that
credential, not guessed at or worked around here.
