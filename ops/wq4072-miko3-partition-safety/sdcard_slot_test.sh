#!/usr/bin/env bash
# wq#4072 step 7 — SD card slot test for MIKO1070XXM3E0408B7's head board. Verifies the
# slot enumerates and can be READ, without ever writing to the inserted card: dd here
# only runs with if=<device> (read direction), matching the fleet-wide "dd never writes
# to a device" rule from the eMMC runbook (agents/work-queue/reports/224_miko_asset_recovery.md).
# NOT run by the staging session — run on the bench host with a test card inserted:
#   ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq4072-miko3-partition-safety/sdcard_slot_test.sh /dev/sdX'
set -uo pipefail
DEV="${1:-}"
OUT_DIR="$(dirname "$0")/logs"
mkdir -p "$OUT_DIR"
TS=$(date -u +%Y%m%d_%H%M%SZ)
LOG="${OUT_DIR}/sdcard_slot_test_${TS}.log"

if [ -z "$DEV" ]; then
  echo "usage: $0 /dev/sdX   (device node for the inserted test card, from lsblk/dmesg after insert)" | tee "$LOG"
  exit 1
fi

{
  echo "== dmesg tail (enumeration check) =="
  dmesg -T | tail -30

  echo
  echo "== lsblk =="
  lsblk "$DEV" 2>&1

  echo
  echo "== read-only probe: first 512 bytes (boot sector) =="
  dd if="$DEV" bs=512 count=1 status=none 2>&1 | xxd | head -5

  echo
  echo "== read-only probe: sequential read, 64MiB for throughput sanity =="
  dd if="$DEV" of=/dev/null bs=1M count=64 2>&1
} | tee "$LOG"

echo "slot test log: $LOG"
