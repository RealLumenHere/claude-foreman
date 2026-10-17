#!/bin/bash
# wq#4102 window op: make the running gateway serve the alias fallback chains
# alias_fallbacks.py wrote into litellm_config.yaml. LiteLLM reads that file only
# at start, so the edit is inert until the process restarts.
# Queued behind wq3115-litellm-3cv6 (which recreates the container). If that op
# already reloaded the config, the first probe passes and this exits without
# touching the container; otherwise it restarts litellm once and re-probes.
# Exit: 0 aliases fall back | 1 still not falling back after restart | 3 precheck abort
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
CONFIG=${CONFIG:-/home/lumen/agent/litellm_config.yaml}
COMPOSE_FILE=${COMPOSE_FILE:-/home/lumen/docker/docker-compose.yml}
SERVICE=${SERVICE:-litellm}
PORT=${PORT:-4000}
HEALTH_WAIT=${HEALTH_WAIT:-180}
MODELS=(lumen/best lumen/fast deepseek-v4-pro)
log() { echo "[wq4102 $(date -u +%H:%M:%SZ)] $*"; }

python3 "$HERE/alias_fallbacks.py" apply "$CONFIG" --write || { log "PRECHECK FAIL: alias_fallbacks apply"; exit 3; }
python3 "$HERE/alias_fallbacks.py" check "$CONFIG" >/dev/null || { log "PRECHECK FAIL: config still missing alias chains"; exit 3; }

if python3 "$HERE/probe_alias_fallback.py" "${MODELS[@]}"; then
  log "running gateway already falls back for every alias; no restart"
  exit 0
fi

log "restarting $SERVICE to load the alias chains"
docker compose -f "$COMPOSE_FILE" restart "$SERVICE" >/dev/null 2>&1 || { log "restart failed"; exit 1; }
deadline=$((SECONDS + HEALTH_WAIT))
until curl -s -m 4 "http://127.0.0.1:$PORT/health/readiness" | grep -q '"db":"connected"'; do
  [ $SECONDS -ge $deadline ] && { log "readiness not healthy within ${HEALTH_WAIT}s"; exit 1; }
  sleep 3
done
python3 "$HERE/probe_alias_fallback.py" "${MODELS[@]}" || { log "VERIFY FAIL: aliases still not falling back"; exit 1; }
log "verified: aliases fall back after restart"
