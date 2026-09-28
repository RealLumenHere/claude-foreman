#!/usr/bin/env bash
# wq#2714 stage 1c helper, hardened in wq#2842: sample temperature/clock/utilization for EVERY
# amdgpu card (or GPU_CARDS) from sysfs+hwmon, plus CPU package temp, and raise a live abort.
# ROCm does not support this card generation (Tahiti/Pitcairn, GCN 1.x), so sysfs/hwmon is the
# whole telemetry surface. Backgrounded by guarded_run.sh; stops when <stop_file> appears.
#
# Live abort (off unless ABORT_TEMP_C is set): a card over ABORT_TEMP_C for ABORT_SAMPLES
# consecutive samples, or the CPU package over CPU_ABORT_C, writes the reason to ABORT_FILE.
# Armed with no readable GPU temp it ALSO aborts: a soak with no thermal visibility on a
# shared-thermal-core chassis must not proceed.
#
# CSV: ts_utc,temp_c,sclk_mhz,busy_pct,card,cpu_temp_c  (one row per card per sample; the
# first four columns keep the wq#2714 layout so existing readers of column 2 still work).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/lib_common.sh"

CSV="${1:?usage: monitor_sysfs.sh <csv_out> <interval_seconds> <stop_file> [card_device_glob]}"
INTERVAL="${2:?usage: monitor_sysfs.sh <csv_out> <interval_seconds> <stop_file> [card_device_glob]}"
STOP_FILE="${3:?usage: monitor_sysfs.sh <csv_out> <interval_seconds> <stop_file> [card_device_glob]}"
DRM_ROOT="${DRM_ROOT:-/sys/class/drm}"
CARD_GLOB="${4:-$DRM_ROOT/card*/device}"
HWMON_ROOT="${HWMON_ROOT:-/sys/class/hwmon}"
ABORT_TEMP_C="${ABORT_TEMP_C:-}"
ABORT_SAMPLES="${ABORT_SAMPLES:-2}"
CPU_ABORT_C="${CPU_ABORT_C:-}"
ABORT_FILE="${ABORT_FILE:-$(dirname "$STOP_FILE")/.thermal_abort}"

cards=()
for d in $CARD_GLOB; do
  [ -e "$d/hwmon" ] || continue
  name=$(basename "$(dirname "$d")")
  if [ -n "${GPU_CARDS:-}" ]; then
    case " $GPU_CARDS " in *" $name "*) ;; *) continue ;; esac
  fi
  cards+=("$d")
done
if [ "${#cards[@]}" -eq 0 ]; then
  log "monitor_sysfs: no card with hwmon found under $CARD_GLOB -- GPU telemetry will be all-NA"
fi

echo "ts_utc,temp_c,sclk_mhz,busy_pct,card,cpu_temp_c" > "$CSV"

over() { awk -v t="$1" -v m="$2" "BEGIN{exit !(t+0>m+0)}"; }   # 0 when t > m

trip() { printf "%s\n" "$1" > "$ABORT_FILE"; log "monitor_sysfs: THERMAL ABORT: $1"; }

cpu_temp() {
  local h t best=""
  for h in "$HWMON_ROOT"/hwmon*; do
    [ -f "$h/name" ] || continue
    [ "$(cat "$h/name" 2>/dev/null)" = coretemp ] || continue
    t=$(cat "$h/temp1_input" 2>/dev/null || true)
    [ -n "$t" ] || continue
    if [ -z "$best" ] || [ "$t" -gt "$best" ]; then best="$t"; fi
  done
  if [ -n "$best" ]; then awk -v r="$best" "BEGIN{printf \"%.1f\", r/1000}"; else echo NA; fi
}

declare -A hot
cpu_hot=0
while [ ! -f "$STOP_FILE" ]; do
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  cpu=$(cpu_temp)
  seen_temp=0
  if [ "${#cards[@]}" -eq 0 ]; then
    echo "$ts,NA,NA,NA,NA,$cpu" >> "$CSV"
  fi
  for d in "${cards[@]+"${cards[@]}"}"; do
    name=$(basename "$(dirname "$d")")
    temp_c="NA"; sclk_mhz="NA"; busy_pct="NA"
    hwmon_dir=$(find "$d/hwmon" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | head -n1 || true)
    if [ -n "$hwmon_dir" ] && [ -f "$hwmon_dir/temp1_input" ]; then
      raw=$(cat "$hwmon_dir/temp1_input" 2>/dev/null || echo "")
      if [ -n "$raw" ]; then temp_c=$(awk -v r="$raw" "BEGIN{printf \"%.1f\", r/1000}"); fi
    fi
    if [ -f "$d/pp_dpm_sclk" ]; then
      picked=$(grep "\*" "$d/pp_dpm_sclk" 2>/dev/null | head -n1 | awk "{print \$2}" | tr -d "Mhz" || true)
      if [ -n "$picked" ]; then sclk_mhz="$picked"; fi
    fi
    if [ -f "$d/gpu_busy_percent" ]; then
      raw_busy=$(cat "$d/gpu_busy_percent" 2>/dev/null || echo "")
      if [ -n "$raw_busy" ]; then busy_pct="$raw_busy"; fi
    fi
    echo "$ts,$temp_c,$sclk_mhz,$busy_pct,$name,$cpu" >> "$CSV"
    if [ "$temp_c" != NA ]; then
      seen_temp=1
      if [ -n "$ABORT_TEMP_C" ] && over "$temp_c" "$ABORT_TEMP_C"; then
        hot[$name]=$(( ${hot[$name]:-0} + 1 ))
        if [ "${hot[$name]}" -ge "$ABORT_SAMPLES" ]; then
          trip "$name ${temp_c}C > ${ABORT_TEMP_C}C for ${ABORT_SAMPLES} consecutive samples"
        fi
      else
        hot[$name]=0
      fi
    fi
  done
  if [ -n "$CPU_ABORT_C" ] && [ "$cpu" != NA ] && over "$cpu" "$CPU_ABORT_C"; then
    cpu_hot=$((cpu_hot + 1))
    if [ "$cpu_hot" -ge "$ABORT_SAMPLES" ]; then trip "cpu package ${cpu}C > ${CPU_ABORT_C}C for ${ABORT_SAMPLES} consecutive samples"; fi
  else
    cpu_hot=0
  fi
  if [ -n "$ABORT_TEMP_C" ] && [ "$seen_temp" -eq 0 ]; then
    trip "no_temp_telemetry: abort armed but no GPU temp is readable"
  fi
  sleep "$INTERVAL"
done
