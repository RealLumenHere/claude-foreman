#!/usr/bin/env bash
# wq#2714 stage 1c helper: sample GPU temperature/clock/utilization from
# sysfs+hwmon while compute_soak.sh drives the compute load, so a headless
# soak can still catch thermal-throttle / clock-instability without a
# monitor attached. ROCm does not support this card generation
# (Tahiti/Pitcairn, GCN 1.0), so sysfs/hwmon is the whole telemetry surface.
# Not meant to be run standalone in production -- compute_soak.sh backgrounds
# it and signals it to stop by creating <stop_file>.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/lib_common.sh"

CSV="${1:?usage: monitor_sysfs.sh <csv_out> <interval_seconds> <stop_file> [card_device_glob]}"
INTERVAL="${2:?usage: monitor_sysfs.sh <csv_out> <interval_seconds> <stop_file> [card_device_glob]}"
STOP_FILE="${3:?usage: monitor_sysfs.sh <csv_out> <interval_seconds> <stop_file> [card_device_glob]}"
CARD_GLOB="${4:-/sys/class/drm/card*/device}"

CARD_DIR=""
for d in $CARD_GLOB; do
  if [ -e "$d/hwmon" ]; then
    CARD_DIR="$d"
    break
  fi
done

if [ -z "$CARD_DIR" ]; then
  log "monitor_sysfs: no card with hwmon found under $CARD_GLOB -- telemetry will be all-NA"
fi

echo "ts_utc,temp_c,sclk_mhz,busy_pct" > "$CSV"

while [ ! -f "$STOP_FILE" ]; do
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  temp_c="NA"
  sclk_mhz="NA"
  busy_pct="NA"

  if [ -n "$CARD_DIR" ]; then
    hwmon_dir=$(find "$CARD_DIR/hwmon" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | head -n1 || true)
    if [ -n "$hwmon_dir" ] && [ -f "$hwmon_dir/temp1_input" ]; then
      raw=$(cat "$hwmon_dir/temp1_input" 2>/dev/null || echo "")
      [ -n "$raw" ] && temp_c=$(awk -v r="$raw" 'BEGIN{printf "%.1f", r/1000}')
    fi
    if [ -f "$CARD_DIR/pp_dpm_sclk" ]; then
      picked=$(grep '\*' "$CARD_DIR/pp_dpm_sclk" 2>/dev/null | head -n1 | awk '{print $2}' | tr -d 'Mhz' || true)
      [ -n "$picked" ] && sclk_mhz="$picked"
    fi
    if [ -f "$CARD_DIR/gpu_busy_percent" ]; then
      raw_busy=$(cat "$CARD_DIR/gpu_busy_percent" 2>/dev/null || echo "")
      [ -n "$raw_busy" ] && busy_pct="$raw_busy"
    fi
  fi

  echo "$ts,$temp_c,$sclk_mhz,$busy_pct" >> "$CSV"
  sleep "$INTERVAL"
done
