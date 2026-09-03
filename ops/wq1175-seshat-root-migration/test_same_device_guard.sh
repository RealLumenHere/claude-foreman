#!/usr/bin/env bash
# wq#1346 — regression test for migrate_root.sh's same-device refusal.
# The original guard digit-stripped only the root device (`${root_dev%[0-9]*}`),
# which turns /dev/nvme0n1p2 into /dev/nvme0n1p and lets TARGET_DEV=/dev/nvme0n1
# — the live root's own disk — pass. Needs no privileges and no real devices:
# every name below is deliberately absent from any host, so base_disk() takes
# its naming-rule fallback and the results are the same everywhere.
set -uo pipefail

# shellcheck source=migrate_root.sh
source "$(dirname "${BASH_SOURCE[0]}")/migrate_root.sh"
set +e   # migrate_root.sh's `set -e` would abort this file on the first expected refusal

fails=0

expect_base() {
  local dev="$1" want="$2" got
  got="$(base_disk "${dev}")"
  if [[ "${got}" == "${want}" ]]; then
    echo "ok   base_disk ${dev} -> ${got}"
  else
    echo "FAIL base_disk ${dev} -> ${got} (want ${want})"
    fails=$((fails + 1))
  fi
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

if (( fails )); then
  echo "${fails} failure(s)"
  exit 1
fi
echo "all checks passed"
