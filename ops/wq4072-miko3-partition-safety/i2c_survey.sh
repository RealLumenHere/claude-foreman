#!/usr/bin/env bash
# wq#4072 step 4 — i2cdetect sweep across every i2c bus on the bench host the Miko 3
# head board is wired to. NOT run by the staging session (no bench host in this
# sandbox's execution path, same gap as wq#349) — run on the bench host once wired:
#   ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq4072-miko3-partition-safety/i2c_survey.sh'
#
# Read-only: i2cdetect -y probes bus addresses, never writes a register. Output goes to
# a timestamped log next to this script so repeated runs don't clobber each other.
set -uo pipefail
OUT_DIR="$(dirname "$0")/logs"
mkdir -p "$OUT_DIR"
TS=$(date -u +%Y%m%d_%H%M%SZ)
LOG="${OUT_DIR}/i2c_survey_${TS}.log"

echo "wq#4072 i2c survey — $TS" | tee "$LOG"

if ! command -v i2cdetect >/dev/null 2>&1; then
  echo "i2cdetect not installed (apt: i2c-tools) — aborting, no bus probed" | tee -a "$LOG"
  exit 1
fi

shopt -s nullglob
buses=(/dev/i2c-*)
if [ ${#buses[@]} -eq 0 ]; then
  echo "no /dev/i2c-* bus found — nothing to probe" | tee -a "$LOG"
  exit 1
fi

for bus_dev in "${buses[@]}"; do
  bus_num="${bus_dev##*i2c-}"
  echo "== bus ${bus_num} ==" | tee -a "$LOG"
  i2cdetect -y "$bus_num" 2>&1 | tee -a "$LOG"
done

echo "done — $(( ${#buses[@]} )) bus(es) probed, log: $LOG"
