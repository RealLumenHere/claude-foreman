#!/bin/bash
# wq#4483 window op: retarget lumen/code off dead isis, land fallback, verify.
# Precheck the replacement target, apply the config edit, restart litellm only
# if the running gateway doesn't already reflect it, then verify with a real
# completion.
# Exit: 0 verified | 1 not completing after restart | 3 precheck abort
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
PROBE=$(cd "$HERE/../wq4135-litellm-fast-fallback-retarget" && pwd)
CONFIG=${CONFIG:-/home/lumen/agent/litellm_config.yaml}
COMPOSE_FILE=${COMPOSE_FILE:-/home/lumen/fleet-config/docker/litellm/docker-compose.yml}
SERVICE=${SERVICE:-litellm}
PORT=${PORT:-4000}
HEALTH_WAIT=${HEALTH_WAIT:-180}
TARGET=${TARGET:-deepseek-v4-flash}
log() { echo "[wq4483 $(date -u +%H:%M:%SZ)] $*"; }

# The retarget target must itself answer, or this just points lumen/code at
# a second dead rung.
python3 "$PROBE/probe_rung.py" "$TARGET" || { log "PRECHECK FAIL: $TARGET is not answering"; exit 3; }

python3 "$HERE/retarget_lumen_code.py" apply "$CONFIG" --target "$TARGET" --write || { log "PRECHECK FAIL: apply"; exit 3; }
python3 "$HERE/retarget_lumen_code.py" check "$CONFIG" >/dev/null || { log "PRECHECK FAIL: config still broken after apply"; exit 3; }

if python3 "$PROBE/probe_rung.py" lumen/code; then
  log "lumen/code already completes on the running gateway; no restart"
  exit 0
fi

log "restarting $SERVICE to load the edited config"
docker compose -f "$COMPOSE_FILE" restart "$SERVICE" >/dev/null 2>&1 || { log "restart failed"; exit 1; }
deadline=$((SECONDS + HEALTH_WAIT))
until curl -s -m 4 "http://127.0.0.1:$PORT/health/readiness" | grep -q '"db":"connected"'; do
  [ $SECONDS -ge $deadline ] && { log "readiness not healthy within ${HEALTH_WAIT}s"; exit 1; }
  sleep 3
done
python3 "$PROBE/probe_rung.py" lumen/code || { log "VERIFY FAIL: lumen/code still not completing"; exit 1; }
log "verified: lumen/code completes after restart (via $TARGET)"
