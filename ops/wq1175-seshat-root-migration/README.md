# wq#1175 — Seshat: move root off the 1TB NVMe, repurpose it as data

Goal: Seshat's Ubuntu 26.04 root currently lives on the 1TB NVMe (ata2). Move
root onto a dedicated boot SSD, then repurpose the NVMe as a data volume for
Nextcloud + the mail store.

## Hardware prerequisite (blocking)

Live diagnosis 2026-09-01 (Frank, both nodes, BIOS settings confirmed
identical): Seshat and Thoth share a board (MTGU3CS-SD, AMI F2 2021-07-29).
Thoth's mSATA slot carries an mSATA SSD and links fine (ata1 up). Seshat's
mSATA slot instead carries a passive mSATA→USB breakout to a USB-powered
2.5in drive, and that drive is **not linking** (ata1 SStatus 4 = offline).
Hypothesis: Flex-IO presence detect at POST misses the unpowered drive and
parks the lane before the OS ever sees it.

Frank is testing two fixes, in order: (1) give the drive external always-on
5V power so it's live at POST, (2) if that doesn't clear it, swap in a bare
mSATA SSD directly in the slot instead of the USB breakout. **The target
boot drive for this runbook is whichever device ends up linked on ata1** —
its `/dev/sdX` name isn't knowable until then, which is why both scripts
below take the device as an argument rather than hardcoding one.

This is a physical-hardware action nobody but Frank can perform (Tier-0
instinct 2: queue as a physical task, don't gate the rest of the work on
it). Filed as **wq#1341** ([physical] Seshat: fix ata1 link — 5V power /
bare mSATA swap), `frank_gated`, so it's tracked independently; this ticket
delivers the runbook so migration can proceed the moment ata1 links, with no
further research pass needed.

## Plan

1. **Clone root onto the new drive** — `migrate_root.sh <target-dev>`
   (`migrate_root.sh:1-99`). Partitions the target as GPT (512MiB ESP +
   ext4 root), rsyncs the live root filesystem across
   (`migrate_root.sh:102-105`), chroots in to install GRUB in UEFI mode and
   rewrite `/etc/fstab` with the new partitions' UUIDs
   (`migrate_root.sh:112-119`). The NVMe stays the live root throughout — this
   step only reads it. Before any of that it refuses to run when the target
   resolves to the same physical disk as the live root
   (`migrate_root.sh:65-76`) — see "Same-device guard" below.

2. **Cutover** — `efibootmgr -v` to confirm the new `ubuntu-seshat` boot
   entry exists, reboot and manually select it once (one-time boot override,
   not a boot-order change) to verify the clone actually boots and networking
   /services come up clean. Seshat is not Thor, so a reboot for this is
   in-scope per Tier-0 instinct 1. Only after that manual verification passes,
   set it as the default entry (`efibootmgr -o <new-entry>,...`).

3. **Repurpose the NVMe** — `repurpose_nvme_data.sh <nvme-dev>`
   (`repurpose_nvme_data.sh:1-49`). Refuses to run if the NVMe is still the
   live root (`repurpose_nvme_data.sh:15-18`) or still mounted
   (`repurpose_nvme_data.sh:19-22`) — cutover must be verified first. Wipes
   it, lays down a single ext4 volume labeled `seshat-data`, mounts it at
   `/srv/data` via a `nofail` fstab entry (so a future NVMe failure can't
   hang boot), and creates `/srv/data/nextcloud` and `/srv/data/mail`
   (`repurpose_nvme_data.sh:43-45`).

4. **Repoint services** — update the Nextcloud container's data-volume
   mount and the mail store's storage path to `/srv/data/nextcloud` and
   `/srv/data/mail` respectively, then restart those services. Left as a
   manual step here because it depends on how Nextcloud/mail are currently
   deployed on Seshat (compose file location, existing data path) — nothing
   in this branch's read-only research surfaced that config, and guessing it
   would violate the "never guess a path" doctrine. Whoever runs the cutover
   should confirm the current mount source with `docker inspect` (or the
   relevant compose file) before repointing.

## Why this order

Clone-then-verify-then-wipe means the NVMe is never touched destructively
until there's a proven-bootable alternative — if the new SSD turns out not
to boot, Seshat still has its original root untouched. `repurpose_nvme_data.sh`
enforces that ordering in code, not just in this doc (see the two refusal
checks above), so a future run can't skip the safety window by accident.

## Same-device guard

`migrate_root.sh` repartitions and reformats whatever device it is handed, so
its one job before that is to notice when the operator has named the live
root's own disk. The original check compared `TARGET_DEV` against
`${root_dev%[0-9]*}`, which strips only the final digit: `/dev/nvme0n1p2`
became `/dev/nvme0n1p`, so `TARGET_DEV=/dev/nvme0n1` — the same drive, under
its correct base-disk name — did not match and the script proceeded to wipe
the running root. The bug was NVMe-specific (the `p` partition separator
survived the strip); `/dev/sda2` vs `/dev/sda` refused correctly. Seshat's
root is on an NVMe, which is precisely the case this runbook is written for.

`base_disks()` (`migrate_root.sh:25-39`) now normalizes *both* names to the
whole disk(s) they physically live on before comparing, and `is_same_disk()`
(`migrate_root.sh:43-54`) refuses when those two sets intersect.

The first version of that fix (wq#1346) used `lsblk -no PKNAME <dev>` and took
the first output line as the device's own parent. wq#1348 reproduced a false
ALLOW from that on real hardware: without `--nodeps`, `lsblk` prints the
device's entire *descendant* subtree, so on the default Ubuntu guided-LVM
layout (LV on a partition on a disk) `base_disk /dev/nvme5n1` returned
`/dev/nvme5n1p3` — a grandchild — and the guard let the operator target the
live root's own disk. `lsblk -srno NAME,TYPE` instead walks the *inverse*
dependency tree — device, its parents, up to whole disks — which resolves the
disk case, the partition case, and the LVM/LUKS/RAID cases the single PKNAME
lookup could not reach at all (an LV has no PKNAME). A RAID array yields
several disks, hence set intersection rather than string equality. The
partition-naming fallback (`p`-separated for nvme/mmcblk/loop/md, bare digits
for sd/vd/hd) still covers nodes that don't exist on the machine running the
check. Where the live root isn't a walkable block device at all (btrfs reports
`/dev/sda2[/@subvol]`, zfs and overlay report no device), the script now
refuses rather than falling through to a string comparison that would allow
(`migrate_root.sh:65-71`).

## Verifying

- `bash ops/wq1175-seshat-root-migration/test_same_device_guard.sh` — 13
  fabricated-device assertions plus a real-device section, no privileges needed
  (it sources `migrate_root.sh`, which returns early when sourced,
  `migrate_root.sh:58`). The fabricated names cover the NVMe case above, the
  SATA control case, `sda10`-style two-digit partitions, and the allow cases the
  migration depends on — but none of them are block devices, so they only ever
  exercise the naming-rule fallback. The real-device section
  (`test_same_device_guard.sh:75-116`) runs the guard against the host's live
  device tree: every disk `base_disks()` reports for the live root must be
  `TYPE=disk`, must resolve to itself, and must be refused as a target, plus an
  unrelated whole disk must still be allowed. On a guided-LVM host that section
  fails against the pre-fix code and passes after.
- `bash -n ops/wq1175-seshat-root-migration/migrate_root.sh` and same for
  `repurpose_nvme_data.sh` — both parse clean.
- Neither script has been run against live Seshat: this build worker has no
  ssh/hardware access (not on the sandbox's Bash allowlist, and the target
  drive doesn't exist yet — see prerequisite above). This ticket delivers
  the verified-safe runbook; execution is wq#1341's follow-on once ata1 links.
