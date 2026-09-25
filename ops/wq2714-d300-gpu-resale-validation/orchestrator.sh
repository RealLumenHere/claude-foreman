#!/usr/bin/env bash
# wq#2714: D300 (Mac Pro 6,1 module) resale-validation orchestrator.
# Runs the three headless auto-test stages in order and stops at the first
# FAIL -- no reason to burn hours of compute-soak time on a card that
# already failed the VRAM test. On a full headless PASS it tells the
# operator to run the manual visual gate (docs/wq2714_d300_gpu_resale_sop.md)
# and how to record that result with record_visual_gate.sh.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/lib_common.sh"

usage() {
  echo "usage: orchestrator.sh --serial SERIAL [--outdir DIR] [--vram-seconds N] [--soak-seconds N] [--max-temp-c N] [--expected-gpu STR]" >&2
  exit 2
}

SERIAL=""
OUTDIR=""
VRAM_SECONDS=600
SOAK_SECONDS=7200
MAX_TEMP_C=95
EXPECTED_GPU="PITCAIRN"

while [ $# -gt 0 ]; do
  case "$1" in
    --serial) SERIAL="$2"; shift 2 ;;
    --outdir) OUTDIR="$2"; shift 2 ;;
    --vram-seconds) VRAM_SECONDS="$2"; shift 2 ;;
    --soak-seconds) SOAK_SECONDS="$2"; shift 2 ;;
    --max-temp-c) MAX_TEMP_C="$2"; shift 2 ;;
    --expected-gpu) EXPECTED_GPU="$2"; shift 2 ;;
    *) usage ;;
  esac
done
[ -n "$SERIAL" ] || usage

RUN_TS=$(date -u +%Y%m%dT%H%M%SZ)
OUTDIR="${OUTDIR:-$HERE/grades/runs/${SERIAL}_${RUN_TS}}"
mkdir -p "$OUTDIR"
GRADE_LOG="$HERE/grades/resale_grades.csv"
mkdir -p "$(dirname "$GRADE_LOG")"
if [ ! -f "$GRADE_LOG" ]; then
  echo "serial,run_ts,stage,result,notes,outdir" > "$GRADE_LOG"
fi

log "wq#2714 resale validation start: serial=$SERIAL outdir=$OUTDIR"

if ! "$HERE/bind_check.sh" "$OUTDIR" "$EXPECTED_GPU"; then
  echo "$SERIAL,$RUN_TS,bind_check,FAIL,\"see $OUTDIR/bind_check.json\",$OUTDIR" >> "$GRADE_LOG"
  log "HEADLESS GRADE: FAIL at bind_check"
  echo "HEADLESS_GRADE=FAIL stage=bind_check outdir=$OUTDIR"
  exit 1
fi

if ! "$HERE/vram_test.sh" "$OUTDIR" "$VRAM_SECONDS"; then
  echo "$SERIAL,$RUN_TS,vram_test,FAIL,\"see $OUTDIR/vram_test.json\",$OUTDIR" >> "$GRADE_LOG"
  log "HEADLESS GRADE: FAIL at vram_test"
  echo "HEADLESS_GRADE=FAIL stage=vram_test outdir=$OUTDIR"
  exit 1
fi

if ! "$HERE/compute_soak.sh" "$OUTDIR" "$SOAK_SECONDS" "$MAX_TEMP_C"; then
  echo "$SERIAL,$RUN_TS,compute_soak,FAIL,\"see $OUTDIR/compute_soak.json\",$OUTDIR" >> "$GRADE_LOG"
  log "HEADLESS GRADE: FAIL at compute_soak"
  echo "HEADLESS_GRADE=FAIL stage=compute_soak outdir=$OUTDIR"
  exit 1
fi

echo "$SERIAL,$RUN_TS,compute_soak,PASS,\"headless clean; manual visual gate pending\",$OUTDIR" >> "$GRADE_LOG"
log "HEADLESS GRADE: PASS -- proceed to manual visual gate"
echo "HEADLESS_GRADE=PASS outdir=$OUTDIR"
echo "NEXT: attach a monitor and run the manual visual gate (FurMark 2 + glmark2 --"
echo "  see docs/wq2714_d300_gpu_resale_sop.md), then record the result with:"
echo "  $HERE/record_visual_gate.sh --serial $SERIAL --run-ts $RUN_TS --result PASS|FAIL --notes \"...\""
