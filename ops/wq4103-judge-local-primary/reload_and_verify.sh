#!/bin/bash
# wq#4103 window op: make the running gateway serve lumen/judge from the local
# primary. judge_local_primary.py edits litellm_config.yaml, but LiteLLM reads
# that file only at process start, so the edit is inert until the container
# restarts. Workers may not touch containers, so this runs as a fleet-window op.
# Idempotent: if the running gateway already serves lumen/judge fast from the
# primary, it exits without restarting.
# Exit: 0 verified | 1 still slow/fallback after restart | 3 precheck abort
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
CONFIG=${CONFIG:-/home/lumen/agent/litellm_config.yaml}
COMPOSE_FILE=${COMPOSE_FILE:-/home/lumen/docker/docker-compose.yml}
SERVICE=${SERVICE:-litellm}
PORT=${PORT:-4000}
HEALTH_WAIT=${HEALTH_WAIT:-180}
log() { echo "[wq4103 $(date -u +%H:%M:%SZ)] $*"; }

python3 "$HERE/judge_local_primary.py" apply "$CONFIG" --write || { log "PRECHECK FAIL: apply"; exit 3; }
python3 "$HERE/judge_local_primary.py" check "$CONFIG" >/dev/null || { log "PRECHECK FAIL: config not local-primary"; exit 3; }

# wq#4148: the ollama/ provider flattens the judge prompt into /api/generate,
# which is what made the 7B false-allow 8/98; serve the rung via ollama_chat/.
# A transport change is invisible to the latency probe, so it forces a restart.
TRANSPORT="$HERE/../wq4148-judge-false-allow/judge_chat_transport.py"
FORCE_RESTART=0
python3 "$TRANSPORT" check "$CONFIG" >/dev/null || FORCE_RESTART=1
python3 "$TRANSPORT" apply "$CONFIG" --write || { log "PRECHECK FAIL: chat transport apply"; exit 3; }
python3 "$TRANSPORT" check "$CONFIG" >/dev/null || { log "PRECHECK FAIL: judge rung not on ollama_chat/"; exit 3; }

if [ "$FORCE_RESTART" = 0 ] && python3 "$HERE/probe_judge_primary.py"; then
  log "running gateway already serves lumen/judge from the local primary; no restart"
else
  log "restarting $SERVICE to load the new lumen/judge routing"
  docker compose -f "$COMPOSE_FILE" restart "$SERVICE" >/dev/null 2>&1 || { log "restart failed"; exit 1; }
  deadline=$((SECONDS + HEALTH_WAIT))
  until curl -s -m 4 "http://127.0.0.1:$PORT/health/readiness" | grep -q '"db":"connected"'; do
    [ $SECONDS -ge $deadline ] && { log "readiness not healthy within ${HEALTH_WAIT}s"; exit 1; }
    sleep 3
  done
  python3 "$HERE/probe_judge_primary.py" || { log "VERIFY FAIL: lumen/judge still slow/fallback"; exit 1; }
  log "verified: lumen/judge served fast from local primary"
fi

# Calibration gate: the local 7B now decides every verdict -- confirm it does not
# wave through a should-deny action before this routing is allowed to stand.
log "calibrating lumen-local-judge on the should-deny corpus"
python3 "$HERE/calibrate_local_judge.py" --model lumen-local-judge || {
  log "CALIBRATION FAIL: qwen2.5-coder:7b false-allowed a should-deny case -- see output"; exit 1; }
log "done: routing live, calibration clean"
