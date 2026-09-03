#!/usr/bin/env bash
# wq#1175 — Seshat: after the new boot SSD (ata1) has been verified bootable
# and is the active root (see README.md "Cutover"), wipe the old 1TB NVMe
# and repurpose it as a single data volume for Nextcloud + the mail store.
#
# Run ON Seshat, as root, AFTER cutover — i.e. `findmnt -n -o SOURCE /` no
# longer points at this NVMe. The script refuses to run otherwise.
set -euo pipefail

NVME_DEV="${1:?usage: repurpose_nvme_data.sh /dev/nvme0n1}"
MOUNT_POINT="/srv/data"
LABEL="seshat-data"

root_dev="$(findmnt -n -o SOURCE /)"
if [[ "${root_dev}" == "${NVME_DEV}"* ]]; then
  echo "refusing: ${NVME_DEV} is still the live root (${root_dev}) — cut over first" >&2
  exit 1
fi
if mount | grep -q "^${NVME_DEV}"; then
  echo "refusing: ${NVME_DEV} (or a partition on it) is already mounted" >&2
  exit 1
fi

echo "== wiping and repartitioning ${NVME_DEV} as a single ext4 data volume =="
wipefs -a "${NVME_DEV}"
parted -s "${NVME_DEV}" mklabel gpt
parted -s "${NVME_DEV}" mkpart data ext4 1MiB 100%
partprobe "${NVME_DEV}"
udevadm settle

data_part="${NVME_DEV}p1"
[[ -b "${data_part}" ]] || data_part="${NVME_DEV}1"

mkfs.ext4 -L "${LABEL}" "${data_part}"
data_uuid="$(blkid -s UUID -o value "${data_part}")"

mkdir -p "${MOUNT_POINT}"
if ! grep -q "UUID=${data_uuid}" /etc/fstab; then
  echo "UUID=${data_uuid} ${MOUNT_POINT} ext4 defaults,nofail 0 2" >> /etc/fstab
fi
mount "${MOUNT_POINT}"

mkdir -p "${MOUNT_POINT}/nextcloud" "${MOUNT_POINT}/mail"
chown 33:33 "${MOUNT_POINT}/nextcloud"   # www-data, matches Nextcloud's default container UID
chmod 750 "${MOUNT_POINT}/mail"

echo "== done: ${data_part} (UUID=${data_uuid}) mounted at ${MOUNT_POINT} =="
echo "Point the Nextcloud container's data volume at ${MOUNT_POINT}/nextcloud"
echo "and the mail store at ${MOUNT_POINT}/mail, then restart those services."
