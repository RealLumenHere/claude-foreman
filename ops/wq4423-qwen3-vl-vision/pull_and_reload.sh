#!/bin/bash
# wq#4423 window op: pull qwen3-vl:8b, retarget lumen-vision, verify on a
# real image. Not runnable from inside a BUILD worker sandbox — ollama pull
# is denied from cron/fworker context (wq#1297: large pulls are manual
# off-peak only) and /home/lumen/agent/litellm_config.yaml sits outside a
# worker's granted roots. Run this by hand (or from a lumen-run unit
# dispatched outside the worker harness) during an off-peak window.
# Exit: 0 verified | 1 not completing after restart | 3 precheck/pull abort
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
CONFIG=${CONFIG:-/home/lumen/agent/litellm_config.yaml}
COMPOSE_FILE=${COMPOSE_FILE:-/home/lumen/fleet-config/docker/litellm/docker-compose.yml}
SERVICE=${SERVICE:-litellm}
PORT=${PORT:-4000}
HEALTH_WAIT=${HEALTH_WAIT:-180}
GROUP=${GROUP:-lumen-vision}
IMAGE=${IMAGE:-}
log() { echo "[wq4423 $(date -u +%H:%M:%SZ)] $*"; }

command -v ollama >/dev/null || { log "PRECHECK FAIL: ollama not on PATH"; exit 3; }
ollama pull qwen3-vl:8b || { log "PULL FAIL: qwen3-vl:8b"; exit 3; }

python3 "$HERE/swap_vision_model.py" apply "$CONFIG" --write || { log "PRECHECK FAIL: apply"; exit 3; }
python3 "$HERE/swap_vision_model.py" check "$CONFIG" || { log "PRECHECK FAIL: config still not on qwen3-vl after apply"; exit 3; }

log "restarting $SERVICE to load the edited config"
docker compose -f "$COMPOSE_FILE" restart "$SERVICE" >/dev/null 2>&1 || { log "restart failed"; exit 1; }
deadline=$((SECONDS + HEALTH_WAIT))
until curl -s -m 4 "http://127.0.0.1:$PORT/health/readiness" | grep -q '"db":"connected"'; do
  [ $SECONDS -ge $deadline ] && { log "readiness not healthy within ${HEALTH_WAIT}s"; exit 1; }
  sleep 3
done

if [ -z "$IMAGE" ]; then
  log "VERIFY SKIPPED: no PCB image path given (set IMAGE=/path/to/pcb.jpg — e.g. a real photo from the Miko dumps)"
  exit 0
fi
python3 "$HERE/probe_vision.py" --image "$IMAGE" || { log "VERIFY FAIL: lumen/vision did not answer on $IMAGE"; exit 1; }
log "verified: lumen/vision (qwen3-vl:8b) answers on $IMAGE"
