#!/usr/bin/env bash
# wq#2714 stage 1c: compute soak with live thermal/clock telemetry.
# ROCm doesn't support GCN1 (Tahiti/Pitcairn/D300), so this drives gpu-burn
# if installed (CUDA-only, so it is never present on an AMD rig), else loops
# clpeak for the soak duration.
#
# wq#2842: the load runs through guarded_run.sh -- clamp lifted on the card under test, telemetry
# for every card plus CPU package, LIVE abort when a card exceeds the limit (this used to be
# checked only after the whole soak), and a guaranteed put-back of the low-power clamp.
# FAIL on: compute tool crash/nonzero exit, thermal abort, max temp over threshold, or an
# error/fail/segfault/xid pattern in the compute tool's own log.
# TOOL_MISSING (rig not fit, card not graded) on: no compute tool, clamp cannot be lifted/restored.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/lib_common.sh"

OUTDIR="${1:?usage: compute_soak.sh <outdir> <duration_seconds> [max_temp_c]}"
DURATION="${2:?usage: compute_soak.sh <outdir> <duration_seconds> [max_temp_c]}"
MAX_TEMP_C="${3:-95}"
RESULT_FILE="$OUTDIR/compute_soak.json"
CSV="$OUTDIR/compute_soak_telemetry.csv"
COMPUTE_LOG="$OUTDIR/compute_soak.log"
SAFETY_S="${SOAK_SAFETY_S:-300}"   # a tool still running this long after its own deadline is hung
mkdir -p "$OUTDIR"

if command -v gpu-burn >/dev/null 2>&1; then
  compute_tool="gpu-burn"
  cmd=(gpu-burn "$DURATION")
elif command -v clpeak >/dev/null 2>&1; then
  compute_tool="clpeak"
  cmd=(bash -c 'deadline=$(( $(date +%s) + $1 )); bad=0; while [ "$(date +%s)" -lt "$deadline" ]; do clpeak || bad=$?; done; exit $bad' _ "$DURATION")
else
  echo "neither gpu-burn nor clpeak found on PATH" > "$COMPUTE_LOG"
  write_result "$RESULT_FILE" "compute_soak" "TOOL_MISSING" "neither gpu-burn nor clpeak on PATH"
  log "compute_soak -> TOOL_MISSING"
  exit 1
fi

set +e
"$HERE/guarded_run.sh" --outdir "$OUTDIR" --tag compute_soak --max-temp-c "$MAX_TEMP_C" \
  --duration "$(( DURATION + SAFETY_S ))" --log "$COMPUTE_LOG" -- "${cmd[@]}"
guard_rc=$?
set -e

case "$guard_rc" in
  98|99)
    why=GPU_HOLD_FAILED
    [ "$guard_rc" = 99 ] && why=GPU_RESTORE_FAILED
    write_result "$RESULT_FILE" "compute_soak" "TOOL_MISSING" "$why: rig cannot hold/restore the GPU power state, card not graded; see $OUTDIR/compute_soak.guard"
    log "compute_soak -> TOOL_MISSING (guard rc $guard_rc)"
    exit 1 ;;
esac

max_temp_seen="NA"
if [ -f "$CSV" ]; then
  max_temp_seen=$(awk -F, 'NR>1 && $2!="NA" {if($2+0>max) max=$2+0; seen=1} END{if(seen) printf "%.1f", max; else print "NA"}' "$CSV")
fi

fail_reasons=""
if [ "$guard_rc" -eq 97 ]; then
  fail_reasons="${fail_reasons}thermal abort: $(cat "$OUTDIR/.compute_soak_thermal_abort" 2>/dev/null); "
elif [ "$guard_rc" -eq 124 ]; then
  fail_reasons="${fail_reasons}compute tool ($compute_tool) still running ${SAFETY_S}s past its deadline (hung); "
elif [ "$guard_rc" -ne 0 ]; then
  fail_reasons="${fail_reasons}compute tool ($compute_tool) exited nonzero ($guard_rc); "
fi
if [ "$max_temp_seen" != "NA" ] && awk -v t="$max_temp_seen" -v m="$MAX_TEMP_C" 'BEGIN{exit !(t>m)}'; then
  fail_reasons="${fail_reasons}max observed temp ${max_temp_seen}C exceeded threshold ${MAX_TEMP_C}C; "
fi
if grep -qiE '\berror|\bfail|segfault|xid' "$COMPUTE_LOG" 2>/dev/null; then
  fail_reasons="${fail_reasons}error/fail pattern found in compute log; "
fi

if [ -n "$fail_reasons" ]; then
  write_result "$RESULT_FILE" "compute_soak" "FAIL" "$fail_reasons(tool=$compute_tool, max_temp=${max_temp_seen}C, log=$COMPUTE_LOG, telemetry=$CSV)"
  log "compute_soak -> FAIL"
  exit 1
fi

write_result "$RESULT_FILE" "compute_soak" "PASS" "ran ${DURATION}s via $compute_tool, max_temp=${max_temp_seen}C, no errors; telemetry=$CSV"
log "compute_soak -> PASS"
