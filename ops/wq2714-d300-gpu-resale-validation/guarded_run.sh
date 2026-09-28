#!/usr/bin/env bash
# wq#2842: run one GPU load with (1) the low-power clamp lifted on the card under test,
# (2) live thermal abort, (3) a guaranteed put-back. Every stress stage goes through here so the
# guard lives in one place, not in each stage script.
#
# usage: guarded_run.sh --outdir DIR --tag NAME --max-temp-c N [--duration S] [--log FILE] -- cmd [args...]
#
# The load runs in its own process group; abort or deadline kills the whole group (TERM, then
# KILL after 10 s). Writes DIR/NAME_telemetry.csv and DIR/NAME.guard (key=value summary).
# Exit: 97 thermal abort | 98 could not lift the clamp | 99 could not restore it |
#       124 deadline reached (like timeout(1)) | otherwise the load own exit code.
# SIGKILL of this script cannot run a trap. The Lumen-side runner therefore starts the rig job
# under systemd-run with ExecStopPost=gpu-lowpower.sh, which restores the clamp regardless.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/lib_common.sh"
set +e
source "$HERE/gpu_hold.sh"

usage() { echo "usage: guarded_run.sh --outdir DIR --tag NAME --max-temp-c N [--duration S] [--log FILE] -- cmd [args...]" >&2; exit 2; }
OUTDIR=""; TAG=""; MAX_TEMP_C=""; DURATION=""; LOG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --outdir) OUTDIR="${2:-}"; shift 2 ;;
    --tag) TAG="${2:-}"; shift 2 ;;
    --max-temp-c) MAX_TEMP_C="${2:-}"; shift 2 ;;
    --duration) DURATION="${2:-}"; shift 2 ;;
    --log) LOG="${2:-}"; shift 2 ;;
    --) shift; break ;;
    *) usage ;;
  esac
done
[ -n "$OUTDIR" ] && [ -n "$TAG" ] && [ -n "$MAX_TEMP_C" ] && [ $# -gt 0 ] || usage
mkdir -p "$OUTDIR"
INTERVAL="${SAMPLE_INTERVAL_S:-5}"
CSV="$OUTDIR/${TAG}_telemetry.csv"
STOP="$OUTDIR/.${TAG}_monitor_stop"
ABORT="$OUTDIR/.${TAG}_thermal_abort"
SUMMARY="$OUTDIR/${TAG}.guard"
rm -f "$STOP" "$ABORT"

mon=""; RUN_PID=""; RESTORE_FAILED=0

stop_monitor() {
  if [ -n "$mon" ]; then touch "$STOP"; wait "$mon" 2>/dev/null; mon=""; fi
}
kill_group() {
  local i
  [ -n "$RUN_PID" ] || return 0
  kill -TERM -- "-$RUN_PID" 2>/dev/null
  for i in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$RUN_PID" 2>/dev/null || return 0
    sleep 1
  done
  kill -KILL -- "-$RUN_PID" 2>/dev/null
}
on_exit() {
  kill_group
  stop_monitor
  gpu_hold_release || RESTORE_FAILED=1
  if [ "$RESTORE_FAILED" = 1 ]; then log "guarded_run: GPU_RESTORE_FAILED -- run: sudo /usr/local/sbin/gpu-lowpower.sh"; exit 99; fi
}
trap on_exit EXIT
trap "exit 143" INT TERM

rc=0; aborted=0; timed_out=0; cmd_rc=""; reason=""

if ! gpu_hold_acquire; then
  rc=98; reason="GPU_HOLD_FAILED: could not lift the low-power clamp on ${GPU_CARDS:-all cards}"
else
  ABORT_TEMP_C="$MAX_TEMP_C" ABORT_FILE="$ABORT" "$HERE/monitor_sysfs.sh" "$CSV" "$INTERVAL" "$STOP" &
  mon=$!
  for i in $(seq 1 30); do
    n=0
    [ -f "$CSV" ] && n=$(wc -l < "$CSV")
    [ "${n:-0}" -ge 2 ] && break
    sleep 1
  done
  if [ -f "$ABORT" ]; then
    aborted=1
  else
    set -m
    if [ -n "$LOG" ]; then "$@" > "$LOG" 2>&1 & else "$@" & fi
    RUN_PID=$!
    deadline=""
    [ -n "$DURATION" ] && deadline=$(( $(date +%s) + DURATION ))
    while kill -0 "$RUN_PID" 2>/dev/null; do
      if [ -f "$ABORT" ]; then aborted=1; kill_group; break; fi
      if [ -n "$deadline" ] && [ "$(date +%s)" -ge "$deadline" ]; then timed_out=1; kill_group; break; fi
      sleep 1
    done
    { wait "$RUN_PID"; cmd_rc=$?; } 2>/dev/null
    RUN_PID=""
    set +m
    [ -f "$ABORT" ] && aborted=1
  fi
  stop_monitor
fi

if ! gpu_hold_release; then RESTORE_FAILED=1; fi

[ -f "$ABORT" ] && reason=$(cat "$ABORT")
max_temp="NA"
if [ -f "$CSV" ]; then
  max_temp=$(awk -F, "NR>1 && \$2!=\"NA\" {if(\$2+0>max) max=\$2+0; seen=1} END{if(seen) printf \"%.1f\", max; else print \"NA\"}" "$CSV")
fi

if   [ "$RESTORE_FAILED" = 1 ]; then rc=99
elif [ "$rc" -eq 98 ];          then rc=98
elif [ "$aborted" = 1 ];        then rc=97
elif [ "$timed_out" = 1 ];      then rc=124
else rc="${cmd_rc:-0}"; fi

{
  echo "rc=$rc"
  echo "cmd_rc=${cmd_rc:-}"
  echo "aborted=$aborted"
  echo "timed_out=$timed_out"
  echo "restore=$([ "$RESTORE_FAILED" = 1 ] && echo FAILED || echo ok)"
  echo "max_temp_c=$max_temp"
  echo "reason=$reason"
} > "$SUMMARY"
log "guarded_run[$TAG]: rc=$rc aborted=$aborted timed_out=$timed_out restore=$([ "$RESTORE_FAILED" = 1 ] && echo FAILED || echo ok) max_temp_c=${max_temp} ${reason:+reason=$reason}"
if [ "$RESTORE_FAILED" = 1 ]; then log "guarded_run: GPU_RESTORE_FAILED -- run: sudo /usr/local/sbin/gpu-lowpower.sh"; fi
trap - EXIT
exit "$rc"
