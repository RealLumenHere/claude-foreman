#!/usr/bin/env bash
# wq#4629 — bridges a lumen-run systemd-unit's terminal state to a filesystem
# event, so a systemd .path unit (inotify, see commander-job-notify.path in
# this bundle) can wake a oneshot drain instead of anything polling.
#
# This script itself blocks for the job's full runtime, so launch it detached
# (lumen-run start / systemd-run / nohup) -- never call it through Commander,
# the exact thing lumen-run wait's own 55s clamp exists to prevent
# (/home/lumen/bin/lumen-run:140-151, reports/wq3264_mcpw_commander_drops_rca.md).
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
usage: lumen-job-notify.sh UNIT [--done-dir DIR] [--poll-cap SEC]
  Polls `lumen-run wait UNIT` to a terminal state (DONE:0 / FAILED:n / GONE)
  and writes a completion marker JSON into --done-dir (default:
  $COMMANDER_WATCHDOG_DONE_DIR or /home/lumen/var/commander-watchdog/done).
  Env overrides (for testing): LUMEN_RUN_BIN (default /home/lumen/bin/lumen-run).
USAGE
  exit 64
}

[ $# -ge 1 ] || usage
unit="$1"; shift
done_dir="${COMMANDER_WATCHDOG_DONE_DIR:-/home/lumen/var/commander-watchdog/done}"
poll_cap=3600
while [ $# -gt 0 ]; do
  case "$1" in
    --done-dir) done_dir="$2"; shift 2;;
    --poll-cap) poll_cap="$2"; shift 2;;
    *) echo "lumen-job-notify: unknown arg $1" >&2; usage;;
  esac
done

lumen_run="${LUMEN_RUN_BIN:-/home/lumen/bin/lumen-run}"

result=""
while :; do
  out=$(LUMEN_RUN_WAIT_CAP="$poll_cap" "$lumen_run" wait "$unit" "$poll_cap" 2>&1)
  case "$out" in
    *TIMEOUT*) continue;;
    *) result="$out"; break;;
  esac
done

mkdir -p "$done_dir"
ts=$(date -u +%FT%TZ)
marker="$done_dir/${unit}.json"
jq -n --arg unit "$unit" --arg result "$result" --arg ts "$ts" \
  '{unit: $unit, result: $result, finished_at: $ts}' > "$marker"
echo "$marker"
