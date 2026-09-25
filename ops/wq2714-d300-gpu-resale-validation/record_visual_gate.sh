#!/usr/bin/env bash
# wq#2714 stage 2 recorder: run by whoever performs the manual visual gate
# (monitor attached, FurMark 2 + glmark2 -- see docs/wq2714_d300_gpu_resale_sop.md)
# after a headless PASS, to close the loop in the same per-card log the
# orchestrator writes to.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/lib_common.sh"

usage() {
  echo "usage: record_visual_gate.sh --serial SERIAL --run-ts RUN_TS --result PASS|FAIL --notes TEXT [--outdir DIR]" >&2
  exit 2
}

SERIAL="" RUN_TS="" RESULT="" NOTES="" OUTDIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --serial) SERIAL="$2"; shift 2 ;;
    --run-ts) RUN_TS="$2"; shift 2 ;;
    --result) RESULT="$2"; shift 2 ;;
    --notes) NOTES="$2"; shift 2 ;;
    --outdir) OUTDIR="$2"; shift 2 ;;
    *) usage ;;
  esac
done
[ -n "$SERIAL" ] && [ -n "$RUN_TS" ] || usage
case "$RESULT" in
  PASS|FAIL) ;;
  *) usage ;;
esac

GRADE_LOG="$HERE/grades/resale_grades.csv"
if [ ! -f "$GRADE_LOG" ]; then
  echo "no grade log at $GRADE_LOG -- run orchestrator.sh for this card first" >&2
  exit 1
fi

echo "$SERIAL,$RUN_TS,visual_gate,$RESULT,\"$NOTES\",${OUTDIR:-}" >> "$GRADE_LOG"
log "recorded visual_gate=$RESULT for serial=$SERIAL run_ts=$RUN_TS"
