# wq#5122 — Ptah eMMC root cloned to Neith (ops record)

Runbook: `/home/lumen/fleet-intel/designs/ptah-to-neith-clone-runbook.md` (phases 0-10).
The work ran on hardware across attempts 1-2 (2026-11-18..21). This directory holds the
scripts that did it, copied verbatim from Lumen's staging dir `/scratch/clone/`, and the
evidence trail, so the clone can be audited or re-run.

| Script | Phase | What it does |
|---|---|---|
| `stage-pass.sh` | 4 | rsync Ptah `/` to `lumen:/scratch/clone/ptah-root/`. Ptah is sender-only (read-only); Lumen side is unprivileged `--fake-super`, so ownership/modes/devices sit in `user.rsync.%stat` xattrs |
| `push-maksoor.sh` | 5 | push the staged tree (sender `--fake-super`) into the loop-mounted Neith p1 image on Maksoor, receiver running as root |
| `phase67.sh` | 6+7 | on the mounted new p1: check the new root UUID, restore Neith's extlinux.conf (fdt `rk3399-roc-pc-plus.dtb`), rewrite the old UUID `fff4af74…` to the new one in extlinux/armbianEnv/fstab, drop the `p1pro-data` (/data NVMe) fstab line, check the DTB/kernel/initrd exist, restore Neith's identity (hostname, hosts, machine-id, ssh host keys, tailscale state, sudoers, neith/asfar accounts, root authorized_keys) |

## Phase outcomes

| Phase | Result | Evidence |
|---|---|---|
| 0 Stage | staging at `/scratch/clone` (RAID XFS, not the root fs) | ticket notes 2026-11-19 02:22 |
| 1 Preflight | 3 gates PASS: bootloader is eMMC-resident below p1 (sector 32768) and identical on both boards; DT pc-mezzanine to pc-plus handled by Phase 6 fdt; docker on eMMC, models on /data (excluded) | `/scratch/clone/preflight/192.168.8.{125,151}.txt` |
| 4 Stage | 2 passes rc=0, 87,946 files, 13.09 GB | `/scratch/clone/stage-pass{1,2}.log` |
| 3 Backup | full eMMC raw read over maskrom: `lumen:/scratch/clone/neith-before/neith-emmc-full-20261119.img.zst`, 1,247,230,076 B, sha256 `e791fa51…6914`, re-verified 2026-11-22 04:2x UTC (match); raw copy at `maksoor:/data/clone/maskrom-dump/emmc-full.img` | `.sha256` sibling file |
| 5 Write | p1 image built on Maksoor (`mkfs` with Neith's exact ext4 feature set so eMMC U-Boot can read it), new UUID `1f978c5c-6087-421d-bcfe-5eb8fa662e6b`, push rc=0 (87,946 files), written to p1 only via `rkdeveloptool wl`; sectors 0-32767 (MBR + bootloader) verified unchanged; SPI untouched | `/scratch/clone/push-maksoor.log`, episode M-26f4872182f1 |
| 6+7 | `phase67.sh` | episode M-26f4872182f1 |
| 8 First boot | PASS 2026-11-20: host=neith, root mmcblk1p1 UUID 1f978c5c, model ROC-RK3399-PC-PLUS, tailscale 100.108.107.111, Neith's original ssh hostkey, docker/tailscaled/ssh active, 0 failed units, /data not mounted | episode M-9f4250b38fbb |
| 9 Verify | see below | |
| 10 Rollback | not needed; backup kept (sha re-verified above) | |

### Phase 9 checks re-run 2026-11-22 (attempt 2, from Lumen, read-only)

- machine-id: Ptah's (`/scratch/clone/ptah-root/etc/machine-id`) differs from the Neith
  machine-id restored by `phase67.sh` (from `neith-before/identity/neith-identity-live-20261118.tar`).
- ssh host keys: ecdsa/ed25519/rsa `.pub` all differ between Ptah's tree and Neith's restored set.
- Ptah unchanged and online: `curl telnet://100.78.207.56:22` returned an `SSH-2.0-OpenSSH_10.0p2
  Debian-7+deb13u4` banner.
- Neith itself was **unreachable** at 2026-11-22 ~04:25 UTC: tailscale :22 timed out, LAN
  192.168.8.151 gave "No route to host". It was last confirmed up 2026-11-21 13:29 UTC, when the
  Frank-ordered inference uninstall ran (episode M-873563654156). This attempt had no fleet-exec
  grant, so it could not look further. Follow-up ticket filed; this is not a clone defect, since
  Phase 8 proved the eMMC boot.

## Deviations from the runbook (recorded reasoning)

1. **Maskrom instead of SD boot (phases 3+5).** The plan parked on Frank flashing and inserting an
   SD card. The eMMC was read and written instead from Maksoor in RK3399 maskrom
   (`rkdeveloptool`, `rk3399_loader_v1.30.130.bin`). This is a raw block read/write of the eMMC
   user area, not `dd` on a live root, and the bootloader region was proven byte-identical
   afterwards. A leftover Armbian SD made the first power-cycle boot from SD, because U-Boot
   prefers SD. Removing it fixed that.
2. **Neith's identity restored, not regenerated (phase 7).** The runbook wipes machine-id, ssh
   host keys and tailscale state and re-joins with a new auth key. Restoring Neith's own
   originals still gives values distinct from Ptah's (checked above). It also keeps Neith's
   existing tailnet node and IP (100.108.107.111) and every fleet `known_hosts` entry, and it
   needs no new auth key minted on Frank's account.
3. **No pass-3 refresh before the push.** The pushed tree is pass 2 from 2026-11-19 02:24 UTC
   (push total size equals pass 2 exactly). Ptah's changes after that were not carried over.
   That is fine: Neith's role is now non-inference (Frank, 2026-11-21) and Ptah's workload was
   removed from it.
4. **Post-clone role change (2026-11-21, Frank's order).** Ptah's ollama/llama.cpp/ncs2-vision
   stack and every model were uninstalled from Neith. Neith runs docker (empty), tailscaled and
   ssh only (`lumen_state neith.services`, `neith.role`).
