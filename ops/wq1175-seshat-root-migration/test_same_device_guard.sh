#!/usr/bin/env bash
# wq#1346 — regression test for migrate_root.sh's same-device refusal.
# The original guard digit-stripped only the root device (`${root_dev%[0-9]*}`),
# which turns /dev/nvme0n1p2 into /dev/nvme0n1p and lets TARGET_DEV=/dev/nvme0n1
# — the live root's own disk — pass. The first table below needs no privileges
# and no real devices: every name in it is deliberately absent from any host, so
# base_disks() takes its naming-rule fallback and the results are the same
# everywhere. The second section (wq#1351) covers the lsblk path that table can
# never reach — read-only, still unprivileged, self-skipping.
set -uo pipefail

# shellcheck source=migrate_root.sh
source "$(dirname "${BASH_SOURCE[0]}")/migrate_root.sh"
set +e   # migrate_root.sh's `set -e` would abort this file on the first expected refusal

fails=0
skips=0

expect_base() {
  local dev="$1" want="$2" got
  got="$(base_disks "${dev}")"
  if [[ "${got}" == "${want}" ]]; then
    echo "ok   base_disks ${dev} -> ${got}"
  else
    echo "FAIL base_disks ${dev} -> ${got} (want ${want})"
    fails=$((fails + 1))
  fi
}

check() {
  local what="$1"
  shift
  if "$@"; then
    echo "ok   ${what}"
  else
    echo "FAIL ${what}"
    fails=$((fails + 1))
  fi
}

skip() {
  echo "skip $1"
  skips=$((skips + 1))
}

expect_guard() {
  local target="$1" root="$2" want="$3" got="allow"
  if is_same_disk "${target}" "${root}"; then got="refuse"; fi
  if [[ "${got}" == "${want}" ]]; then
    echo "ok   target=${target} root=${root} -> ${got}"
  else
    echo "FAIL target=${target} root=${root} -> ${got} (want ${want})"
    fails=$((fails + 1))
  fi
}

expect_base /dev/nvme9n9p2 /dev/nvme9n9
expect_base /dev/nvme9n9   /dev/nvme9n9
expect_base /dev/sdz2      /dev/sdz
expect_base /dev/sdz10     /dev/sdz
expect_base /dev/sdz       /dev/sdz
expect_base /dev/mmcblk9p1 /dev/mmcblk9

# The wq#1346 bug: Seshat's root is on an NVMe, so this is the case that must refuse.
expect_guard /dev/nvme9n9   /dev/nvme9n9p2 refuse
expect_guard /dev/nvme9n9p1 /dev/nvme9n9p2 refuse
# Control: SATA root, which the original guard already caught.
expect_guard /dev/sdz       /dev/sdz2      refuse
expect_guard /dev/sdz10     /dev/sdz2      refuse
# The intended workflow — a genuinely different drive — must still be allowed.
expect_guard /dev/sdz       /dev/nvme9n9p2 allow
expect_guard /dev/nvme9n8   /dev/nvme9n9p2 allow
expect_guard /dev/sdy       /dev/sdz2      allow

# --- real devices: the lsblk path the table above cannot reach (wq#1351) ------
# None of the names above are block devices, so base_disks() always takes its
# naming-rule fallback and the lsblk lookup shipped with zero coverage — which
# is exactly where wq#1348 found a false ALLOW (LVM-on-partition root: the old
# `lsblk -no PKNAME` printed the disk's descendant subtree, and taking line one
# yielded a grandchild's parent instead of the disk's own empty PKNAME). These
# checks run the guard against whatever this host's live device tree offers.
real_device_checks() {
  local root_dev root_disks disk other cand
  root_dev="$(findmnt -n -o SOURCE / 2>/dev/null || true)"
  if [[ ! -b "${root_dev}" ]]; then
    skip "live root (${root_dev:-unresolved}) is not a block device on this host"
    return
  fi

  root_disks="$(base_disks "${root_dev}")"
  echo "info live root ${root_dev} -> $(printf '%s' "${root_disks}" | tr '\n' ' ')"

  while read -r disk; do
    check "base_disks ${root_dev} resolves to whole disk ${disk}" \
      test "$(lsblk -dno TYPE "${disk}" 2>/dev/null)" = disk
    check "base_disks ${disk} -> itself" \
      test "$(base_disks "${disk}")" = "${disk}"
    expect_guard "${disk}" "${root_dev}" refuse
  done <<< "${root_disks}"

  # Over-refusal control: a whole disk the live root does not sit on must pass.
  other=""
  while read -r cand; do
    if ! printf '%s\n' "${root_disks}" | grep -qxF "/dev/${cand}"; then
      other="/dev/${cand}"
      break
    fi
  done < <(lsblk -dno NAME,TYPE 2>/dev/null | awk '$2 == "disk" { print $1 }')
  if [[ -n "${other}" ]]; then
    expect_guard "${other}" "${root_dev}" allow
  else
    skip "host has no unrelated whole disk for the allow control"
  fi
}

real_device_checks

if (( fails )); then
  echo "${fails} failure(s), ${skips} skipped"
  exit 1
fi
echo "all checks passed (${skips} skipped)"
