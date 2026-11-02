#!/bin/bash
# wq#4808 window op: give gemini-flash a live fallback rung.
# Exit: 0 verified | 1 not falling back after restart | 3 precheck abort
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
WQ4102=$(cd "$HERE/../wq4102-litellm-alias-fallbacks" && pwd)
CONFIG=${CONFIG:-/home/lumen/agent/litellm_config.yaml}
COMPOSE_FILE=${COMPOSE_FILE:-/home/lumen/docker/docker-compose.yml}
SERVICE=${SERVICE:-litellm}
PORT=${PORT:-4000}
HEALTH_WAIT=${HEALTH_WAIT:-180}
GROUP=${GROUP:-gemini-flash}
TO=${TO:-deepseek-v4-flash}
MODELS=(gemini-flash)
log() { echo "[wq4808 $(date -u +%H:%M:%SZ)] $*"; }

# The target rung must itself answer (directly or via its own fallback), or
# we are pointing gemini-flash at a new dead end.
python3 "$WQ4102/probe_alias_fallback.py" "$TO" || { log "PRECHECK FAIL: target rung $TO is not answering (direct or via its own fallback)"; exit 3; }

python3 "$HERE/add_fallback.py" apply "$CONFIG" --group "$GROUP" --to "$TO" --write || { log "PRECHECK FAIL: add_fallback apply"; exit 3; }
python3 "$HERE/add_fallback.py" check "$CONFIG" --group "$GROUP" >/dev/null || { log "PRECHECK FAIL: config still missing $GROUP"; exit 3; }

if python3 "$WQ4102/probe_alias_fallback.py" "${MODELS[@]}"; then
  log "running gateway already falls back for $GROUP; no restart"
  exit 0
fi

log "restarting $SERVICE to load the new fallback entry"
docker compose -f "$COMPOSE_FILE" restart "$SERVICE" >/dev/null 2>&1 || { log "restart failed"; exit 1; }
deadline=$((SECONDS + HEALTH_WAIT))
until curl -s -m 4 "http://127.0.0.1:$PORT/health/readiness" | grep -q '"db":"connected"'; do
  [ $SECONDS -ge $deadline ] && { log "readiness not healthy within ${HEALTH_WAIT}s"; exit 1; }
  sleep 3
done
python3 "$WQ4102/probe_alias_fallback.py" "${MODELS[@]}" || { log "VERIFY FAIL: still not falling back"; exit 1; }
log "verified: $GROUP falls back to $TO after restart"
