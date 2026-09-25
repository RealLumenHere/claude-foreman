#!/usr/bin/env bash
# mcp-gateway-watchdog.sh (ANUBIS) — wq#2607 T2, per MCP_CONTROL_PLANE_HA_DESIGN.md
# §3.3. Node-local self-heal: 2 consecutive failed responsiveness probes restart
# dc-gateway; a restart cooldown and a flapping guard stop it from restart-looping.
# Run every 1 min by mcp-gateway-watchdog.timer (this directory).
#
# State lives in /home/anubis/.local/state/mcp-gateway-watchdog/ (created on first run):
#   fail_count       - consecutive probe failures since last success/restart
#   last_restart     - unix epoch of the last restart this script issued
#   restart_log      - one epoch timestamp per line, pruned to the last 30 min
#   flapping         - present => auto-restart is suspended; delete to resume
set -euo pipefail

STATE_DIR=/home/anubis/.local/state/mcp-gateway-watchdog
FAIL_COUNT_F="$STATE_DIR/fail_count"
LAST_RESTART_F="$STATE_DIR/last_restart"
RESTART_LOG_F="$STATE_DIR/restart_log"
FLAPPING_F="$STATE_DIR/flapping"

COOLDOWN_S=300          # §3.3: cooldown 300s between restarts
FLAP_WINDOW_S=1800      # >2 restarts/30min => flapping
FLAP_THRESHOLD=2
DC_PROBE_TIMEOUT_S=10
CF_METRICS_URL="http://localhost:20241/ready"   # cloudflared --metrics localhost:20241
NTFY_TOPIC="lumen-reciprocal-watch-x7q2"        # independent sink, §3.4

mkdir -p "$STATE_DIR"
[ -f "$FAIL_COUNT_F" ] || echo 0 > "$FAIL_COUNT_F"

now=$(date +%s)

alert() {
  # Best effort only — an alert failure must never change this script's exit code.
  local priority="$1" title="$2" message="$3"
  curl -fsS -m 10 \
    -H "Title: $title" -H "Priority: $priority" \
    -d "$message" "https://ntfy.sh/$NTFY_TOPIC" >/dev/null 2>&1 || true
}

if [ -f "$FLAPPING_F" ]; then
  echo "mcp-gateway-watchdog: flapping guard active (rm $FLAPPING_F to resume auto-restart)"
  exit 0
fi

dc_ok=0
if curl -fsS -m "$DC_PROBE_TIMEOUT_S" -X POST \
     -H 'Content-Type: application/json' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
     http://localhost:3100/mcp >/dev/null 2>&1; then
  dc_ok=1
fi

cf_ok=1
if ! curl -fsS -m "$DC_PROBE_TIMEOUT_S" "$CF_METRICS_URL" >/dev/null 2>&1; then
  cf_ok=0
fi

if [ "$dc_ok" -eq 1 ] && [ "$cf_ok" -eq 1 ]; then
  echo 0 > "$FAIL_COUNT_F"
  exit 0
fi

fail_count=$(cat "$FAIL_COUNT_F" 2>/dev/null || echo 0)
fail_count=$((fail_count + 1))
echo "$fail_count" > "$FAIL_COUNT_F"

if [ "$fail_count" -lt 2 ]; then
  echo "mcp-gateway-watchdog: probe fail #$fail_count (dc_ok=$dc_ok cf_ok=$cf_ok), waiting for a second consecutive fail before restart"
  exit 0
fi

last_restart=$(cat "$LAST_RESTART_F" 2>/dev/null || echo 0)
if [ $((now - last_restart)) -lt "$COOLDOWN_S" ]; then
  echo "mcp-gateway-watchdog: in cooldown ($((now - last_restart))s since last restart < ${COOLDOWN_S}s), not restarting yet"
  exit 0
fi

unit=dc-gateway
[ "$cf_ok" -eq 0 ] && unit=cloudflared

echo "$now" >> "$RESTART_LOG_F"
awk -v cutoff="$((now - FLAP_WINDOW_S))" '$1 >= cutoff' "$RESTART_LOG_F" > "$RESTART_LOG_F.tmp" && mv "$RESTART_LOG_F.tmp" "$RESTART_LOG_F"
restarts_in_window=$(wc -l < "$RESTART_LOG_F")

if [ "$restarts_in_window" -gt "$FLAP_THRESHOLD" ]; then
  touch "$FLAPPING_F"
  alert high "mcp-gateway-watchdog: anubis FLAPPING" \
    "$restarts_in_window restarts of $unit in the last 30min — auto-restart suspended, opened for manual triage"
  echo "mcp-gateway-watchdog: flapping ($restarts_in_window restarts/30min), suspending auto-restart"
  exit 1
fi

echo "$now" > "$LAST_RESTART_F"
sudo -n systemctl restart "$unit"
alert default "mcp-gateway-watchdog: anubis restarted $unit" \
  "dc_ok=$dc_ok cf_ok=$cf_ok fail_count=$fail_count restarts_in_window=$restarts_in_window"
echo 0 > "$FAIL_COUNT_F"
