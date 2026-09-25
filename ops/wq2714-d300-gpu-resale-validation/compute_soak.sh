#!/usr/bin/env bash
# wq#2714 stage 1c: compute soak with live thermal/clock telemetry.
# ROCm doesn't support GCN1 (Tahiti/Pitcairn/D300), so this drives gpu-burn
# if installed, else loops clpeak for the soak duration, while
# monitor_sysfs.sh watches sysfs/hwmon in the background the whole time.
# FAIL on: compute tool crash/nonzero exit, max temp over threshold, or an
# error/fail/segfault/xid pattern in the compute tool's own log.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/lib_common.sh"

OUTDIR="${1:?usage: compute_soak.sh <outdir> <duration_seconds> [max_temp_c]}"
DURATION="${2:?usage: compute_soak.sh <outdir> <duration_seconds> [max_temp_c]}"
MAX_TEMP_C="${3:-95}"
SAMPLE_INTERVAL="${SAMPLE_INTERVAL_S:-5}"
RESULT_FILE="$OUTDIR/compute_soak.json"
CSV="$OUTDIR/compute_soak_telemetry.csv"
STOP_FILE="$OUTDIR/.monitor_stop"
COMPUTE_LOG="$OUTDIR/compute_soak.log"
mkdir -p "$OUTDIR"
rm -f "$STOP_FILE"

"$HERE/monitor_sysfs.sh" "$CSV" "$SAMPLE_INTERVAL" "$STOP_FILE" &
monitor_pid=$!

compute_tool=""
if command -v gpu-burn >/dev/null 2>&1; then
  compute_tool="gpu-burn"
elif command -v clpeak >/dev/null 2>&1; then
  compute_tool="clpeak"
fi

set +e
if [ "$compute_tool" = "gpu-burn" ]; then
  gpu-burn "$DURATION" > "$COMPUTE_LOG" 2>&1
  compute_rc=$?
elif [ "$compute_tool" = "clpeak" ]; then
  : > "$COMPUTE_LOG"
  deadline=$(( $(date +%s) + DURATION ))
  compute_rc=0
  while [ "$(date +%s)" -lt "$deadline" ]; do
    clpeak >> "$COMPUTE_LOG" 2>&1
    rc=$?
    [ "$rc" -ne 0 ] && compute_rc=$rc
  done
else
  compute_rc=127
  echo "neither gpu-burn nor clpeak found on PATH" > "$COMPUTE_LOG"
fi
set -e

touch "$STOP_FILE"
wait "$monitor_pid" 2>/dev/null || true

if [ -z "$compute_tool" ]; then
  write_result "$RESULT_FILE" "compute_soak" "TOOL_MISSING" "neither gpu-burn nor clpeak on PATH"
  log "compute_soak -> TOOL_MISSING"
  exit 1
fi

max_temp_seen="NA"
if [ -f "$CSV" ]; then
  max_temp_seen=$(awk -F, 'NR>1 && $2!="NA" {if($2+0>max) max=$2+0} END{if(max=="") print "NA"; else printf "%.1f", max}' "$CSV")
fi

fail_reasons=""
if [ "${compute_rc:-1}" -ne 0 ]; then
  fail_reasons="${fail_reasons}compute tool ($compute_tool) exited nonzero ($compute_rc); "
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
