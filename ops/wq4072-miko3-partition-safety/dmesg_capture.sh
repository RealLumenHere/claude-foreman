#!/usr/bin/env bash
# wq#4072 step 6 — full dmesg capture on the bench host, before any flash attempt on
# MIKO1070XXM3E0408B7. Read-only (`dmesg` without -c, so the kernel ring buffer is
# never cleared). NOT run by the staging session — run on the bench host once the unit
# is attached:
#   ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq4072-miko3-partition-safety/dmesg_capture.sh'
set -uo pipefail
OUT_DIR="$(dirname "$0")/logs"
mkdir -p "$OUT_DIR"
TS=$(date -u +%Y%m%d_%H%M%SZ)
LOG="${OUT_DIR}/dmesg_${TS}.log"

dmesg -T > "$LOG" 2>&1
LINES=$(wc -l < "$LOG")
echo "captured ${LINES} lines -> $LOG"
