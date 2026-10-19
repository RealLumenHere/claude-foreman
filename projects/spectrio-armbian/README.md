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

The RE session that produced this bring-up ran on the fleet's dedicated RE
workstation (internal hostname "maksoor"), and the primary artifacts — the
full board `.dtb`/`.dts`, the exact per-unit flash/install transcripts, and
photos of the board/PHY — live there, not in this git repo. This pass
reconstructed and verified the technical content below from the fleet's own
internal documentation of the same bring-up (`projects/spectrio-tf1/golden/`
and `projects/spectrio-tf1/census/CENSUS_METHODOLOGY.md`, both outside this
repo's worktree) plus public upstream sources (cited inline). Pulling the
actual binary artifacts across requires fleet-exec access to maksoor, which
this worker's dispatch did not grant (`fleet-exec run maksoor` →
`DENY [grant:grant_missing_env]` — same class of gap as ISSUE-LOG #192,
wq#1444: the ticket needs a `needs:ssh(maksoor)` tag at dispatch to mint
that grant). Follow-up ticket filed for that pull; see `ops/wq4318-*`.

Before pushing this to a public, Lumen-external GitHub repo: creating a new
*public* repository and the initial push both need a GitHub credential this
worker's `gh` allowlist doesn't carry (only `gh pr/issue/run` read
subcommands are permitted) — queued as a follow-up for whoever holds that
credential, not guessed at or worked around here.
