#!/bin/bash
# wq#4135 window op: make the lumen/fast chain land on a live rung.
# Supersedes window op wq4102-litellm-alias-reload: that op's config edit is
# already live (04:37Z 2026-10-17 restart) but its verify probes lumen/fast,
# which cannot pass while the chain's only rung (cerebras-llama, 402 since the
# Cerebras billing lapse) is dead — it would fail the drain. This op does the
# superset: re-assert alias chains (idempotent), swap the dead rung for
# openrouter-free, restart once if needed, and probe every touched name.
# Exit: 0 verified | 1 not falling back after restart | 3 precheck abort
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
WQ4102=$(cd "$HERE/../wq4102-litellm-alias-fallbacks" && pwd)
CONFIG=${CONFIG:-/home/lumen/agent/litellm_config.yaml}
COMPOSE_FILE=${COMPOSE_FILE:-/home/lumen/docker/docker-compose.yml}
SERVICE=${SERVICE:-litellm}
PORT=${PORT:-4000}
HEALTH_WAIT=${HEALTH_WAIT:-180}
DEAD=${DEAD:-cerebras-llama}
TO=${TO:-openrouter-free}
MODELS=(lumen/fast lumen-lfm2-1b lumen/best deepseek-v4-pro)
log() { echo "[wq4135 $(date -u +%H:%M:%SZ)] $*"; }

# The replacement rung must itself answer, or we are retargeting onto a new dead rung.
python3 "$HERE/probe_rung.py" "$TO" || { log "PRECHECK FAIL: replacement rung $TO is not answering"; exit 3; }

python3 "$WQ4102/alias_fallbacks.py" apply "$CONFIG" --write || { log "PRECHECK FAIL: alias_fallbacks apply"; exit 3; }
python3 "$HERE/retarget_rung.py" apply "$CONFIG" --dead "$DEAD" --to "$TO" --write || { log "PRECHECK FAIL: retarget apply"; exit 3; }
python3 "$HERE/retarget_rung.py" check "$CONFIG" --dead "$DEAD" >/dev/null || { log "PRECHECK FAIL: config still lists $DEAD"; exit 3; }

if python3 "$WQ4102/probe_alias_fallback.py" "${MODELS[@]}"; then
  log "running gateway already falls back for every probed name; no restart"
  exit 0
fi

log "restarting $SERVICE to load the retargeted chains"
docker compose -f "$COMPOSE_FILE" restart "$SERVICE" >/dev/null 2>&1 || { log "restart failed"; exit 1; }
deadline=$((SECONDS + HEALTH_WAIT))
until curl -s -m 4 "http://127.0.0.1:$PORT/health/readiness" | grep -q '"db":"connected"'; do
  [ $SECONDS -ge $deadline ] && { log "readiness not healthy within ${HEALTH_WAIT}s"; exit 1; }
  sleep 3
done
python3 "$WQ4102/probe_alias_fallback.py" "${MODELS[@]}" || { log "VERIFY FAIL: still not falling back"; exit 1; }
log "verified: every probed name falls back to a live rung after restart"
