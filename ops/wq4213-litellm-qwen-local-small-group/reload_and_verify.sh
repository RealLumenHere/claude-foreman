#!/bin/bash
# wq#4213 window op: land the qwen-local-small dedupe+fallback edit.
# Precheck the replacement rung, apply the config edit, restart litellm only
# if the running gateway doesn't already reflect it, then verify.
# Exit: 0 verified | 1 not completing after restart | 3 precheck abort
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
WQ4135=$(cd "$HERE/../wq4135-litellm-fast-fallback-retarget" && pwd)
CONFIG=${CONFIG:-/home/lumen/agent/litellm_config.yaml}
COMPOSE_FILE=${COMPOSE_FILE:-/home/lumen/docker/docker-compose.yml}
SERVICE=${SERVICE:-litellm}
PORT=${PORT:-4000}
HEALTH_WAIT=${HEALTH_WAIT:-180}
GROUP=${GROUP:-qwen-local-small}
FALLBACK=${FALLBACK:-lumen-local-judge}
log() { echo "[wq4213 $(date -u +%H:%M:%SZ)] $*"; }

# The fallback target must itself answer (not via its own nested fallback),
# or this just points qwen-local-small at a second dead rung.
python3 "$WQ4135/probe_rung.py" "$FALLBACK" || { log "PRECHECK FAIL: $FALLBACK is not answering"; exit 3; }

python3 "$HERE/dedupe_qwen_local_small.py" apply "$CONFIG" --write || { log "PRECHECK FAIL: apply"; exit 3; }
python3 "$HERE/dedupe_qwen_local_small.py" check "$CONFIG" >/dev/null || { log "PRECHECK FAIL: config still broken after apply"; exit 3; }

# completes.py accepts a fallback hop (unlike probe_rung's answered_itself) —
# that's the point of the fallback this op just added.
if python3 "$HERE/completes.py" "$GROUP"; then
  log "$GROUP already completes on the running gateway; no restart"
  exit 0
fi

log "restarting $SERVICE to load the edited config"
docker compose -f "$COMPOSE_FILE" restart "$SERVICE" >/dev/null 2>&1 || { log "restart failed"; exit 1; }
deadline=$((SECONDS + HEALTH_WAIT))
until curl -s -m 4 "http://127.0.0.1:$PORT/health/readiness" | grep -q '"db":"connected"'; do
  [ $SECONDS -ge $deadline ] && { log "readiness not healthy within ${HEALTH_WAIT}s"; exit 1; }
  sleep 3
done
python3 "$HERE/completes.py" "$GROUP" || { log "VERIFY FAIL: $GROUP still not completing"; exit 1; }
log "verified: $GROUP completes after restart (directly or via $FALLBACK)"
