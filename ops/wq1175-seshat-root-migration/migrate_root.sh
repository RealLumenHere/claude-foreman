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

TARGET_DEV="${1:?usage: migrate_root.sh /dev/sdX   # the drive that links on ata1}"
ROOT_SRC="/"
ESP_SIZE_MIB=512

root_dev="$(findmnt -n -o SOURCE /)"
if [[ "${TARGET_DEV}" == "${root_dev%[0-9]*}"* ]]; then
  echo "refusing: ${TARGET_DEV} looks like the current root device (${root_dev})" >&2
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
