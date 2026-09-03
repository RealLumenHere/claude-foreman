#!/usr/bin/env bash
# wq#1175 — Seshat: clone the live Ubuntu 26.04 root from the 1TB NVMe (ata2)
# onto the new boot SSD that links on ata1, then make ata1 bootable.
#
# Run ON Seshat, as root, AFTER the ata1 hardware blocker is resolved (see
# README.md "Hardware prerequisite" — Frank's 5V-power / bare-mSATA test).
# This step does not touch the NVMe beyond reading it; the NVMe stays the
# live root until the new drive is verified bootable (see cutover section
# of README.md).
set -euo pipefail

# Resolve a device node to the whole disk(s) it physically lives on, one per line:
#   /dev/nvme0n1p2 -> /dev/nvme0n1, /dev/sda -> /dev/sda,
#   /dev/mapper/vg-lv (LVM on a partition) -> that partition's disk,
#   /dev/md0 (RAID over three NVMes) -> all three.
# `lsblk -s` walks the *inverse* dependency tree (the device, then its parents,
# up to whole disks). The plain `lsblk -no PKNAME` this replaced printed the
# device's whole *descendant* subtree instead, so "take the first line" picked a
# grandchild's parent on any stacked layout — on the default Ubuntu guided-LVM
# root, `base_disk /dev/nvme5n1` returned `/dev/nvme5n1p3` and the guard ALLOWED
# wiping the live root's own disk (wq#1348, reproduced on real hardware).
# The naming-rule fallback covers nodes lsblk can't resolve (absent node, or /
# on a non-block source); NVMe/mmcblk/loop/md separate the partition number with
# a 'p', sd/vd/hd do not.
base_disks() {
  local dev="$1" disks=""
  if [[ -b "${dev}" ]]; then
    disks="$(lsblk -srno NAME,TYPE "${dev}" 2>/dev/null | awk '$2 == "disk" { print "/dev/" $1 }' | sort -u || true)"
  fi
  if [[ -n "${disks}" ]]; then
    printf '%s\n' "${disks}"
  elif [[ "${dev}" =~ ^(/dev/(nvme[0-9]+n[0-9]+|mmcblk[0-9]+|loop[0-9]+|md[0-9]+))p[0-9]+$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
  elif [[ "${dev}" =~ ^(/dev/[a-z]+)[0-9]+$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
  else
    printf '%s\n' "${dev}"
  fi
}

# True when the two nodes share any physical disk. Set intersection, not string
# equality: a RAID member disk is "the same disk" as the array built on top of it.
is_same_disk() {
  local -a target_disks root_disks
  local t r
  mapfile -t target_disks < <(base_disks "$1")
  mapfile -t root_disks < <(base_disks "$2")
  for t in "${target_disks[@]}"; do
    for r in "${root_disks[@]}"; do
      [[ "${t}" == "${r}" ]] && return 0
    done
  done
  return 1
}

# Sourced by test_same_device_guard.sh: stop here with the guard helpers defined
# and nothing destructive run.
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then return 0; fi

TARGET_DEV="${1:?usage: migrate_root.sh /dev/sdX   # the drive that links on ata1}"
ROOT_SRC="/"
ESP_SIZE_MIB=512

root_dev="$(findmnt -n -o SOURCE /)"
# Fail closed: if the live root isn't a block device we can walk (btrfs prints a
# `/dev/sda2[/@subvol]` source, zfs/overlay print no device at all), base_disks()
# would drop to its naming-rule fallback and compare two unrelated strings, which
# ALLOWS. Refusing is the only safe answer when the guard can't be evaluated.
if [[ ! -b "${root_dev}" ]]; then
  echo "refusing: live root source (${root_dev}) is not a block device, so the same-disk guard cannot be evaluated" >&2
  exit 1
fi
if is_same_disk "${TARGET_DEV}" "${root_dev}"; then
  echo "refusing: ${TARGET_DEV} is the disk the current root lives on (${root_dev} -> $(base_disks "${root_dev}" | tr '\n' ' '))" >&2
  exit 1
fi
if mount | grep -q "^${TARGET_DEV}"; then
  echo "refusing: ${TARGET_DEV} (or a partition on it) is already mounted" >&2
  exit 1
fi

echo "== partitioning ${TARGET_DEV} (GPT: ESP + root) =="
parted -s "${TARGET_DEV}" mklabel gpt
parted -s "${TARGET_DEV}" mkpart ESP fat32 1MiB "${ESP_SIZE_MIB}MiB"
parted -s "${TARGET_DEV}" set 1 esp on
parted -s "${TARGET_DEV}" mkpart root ext4 "${ESP_SIZE_MIB}MiB" 100%
partprobe "${TARGET_DEV}"
udevadm settle

esp_part="${TARGET_DEV}1"
root_part="${TARGET_DEV}2"

mkfs.vfat -F32 -n ESP "${esp_part}"
mkfs.ext4 -L rootfs "${root_part}"

mnt="/mnt/wq1175-newroot"
mkdir -p "${mnt}"
mount "${root_part}" "${mnt}"
mkdir -p "${mnt}/boot/efi"
mount "${esp_part}" "${mnt}/boot/efi"

echo "== rsync live root -> ${root_part} =="
rsync -aAXH --numeric-ids \
  --exclude={"/proc/*","/sys/*","/dev/*","/run/*","/tmp/*","/mnt/*","/media/*","/lost+found"} \
  "${ROOT_SRC}" "${mnt}/"

for d in dev proc sys run; do mount --bind "/${d}" "${mnt}/${d}"; done

new_root_uuid="$(blkid -s UUID -o value "${root_part}")"
new_esp_uuid="$(blkid -s UUID -o value "${esp_part}")"

chroot "${mnt}" /bin/bash -c "
  set -euo pipefail
  sed -i '/[[:space:]]\/[[:space:]]/d; /[[:space:]]\/boot\/efi[[:space:]]/d' /etc/fstab
  echo \"UUID=${new_root_uuid} /        ext4 defaults 0 1\" >> /etc/fstab
  echo \"UUID=${new_esp_uuid} /boot/efi vfat umask=0077 0 1\" >> /etc/fstab
  grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=ubuntu-seshat --recheck
  update-grub
"

for d in run sys proc dev; do umount "${mnt}/${d}"; done
umount "${mnt}/boot/efi"
umount "${mnt}"

echo "== done: ${TARGET_DEV} carries a bootable clone of the live root =="
echo "Next: efibootmgr -v to confirm the new 'ubuntu-seshat' entry exists,"
echo "then reboot and select it once to verify before changing boot order."
