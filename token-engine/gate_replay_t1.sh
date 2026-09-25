#!/usr/bin/env bash
# wq#2271 (T1) gate replay: "a push from a worktree succeeds with a
# userinfo-free remote; grep of git config and env finds no token; the sweep
# reports clean." Runs an env-scoped daemon as the calling uid against a
# loopback fake forge (git http-backend behind Basic auth) — no root, no
# network, no real credential:
#
#   1. fake forge up, bare lumen/proj.git behind Basic lumen:<token>
#   2. env-scoped daemon up, engine store holds the token
#   3. client repo: userinfo-free remote + the exact per-worktree config
#      fworker writes (helper reset -> token-engine, useHttpPath)
#   4. git push          -> helper -> broker -> one-shot handoff -> 200, ref lands
#   5. git config grep, process env grep, remote_credential_sweep: no token
#   6. handoff dir empty (the one-shot file was consumed)
#
#   bash token-engine/gate_replay_t1.sh [token-engine src dir]
set -uo pipefail

SRC="${1:-$(cd "$(dirname "$0")" && pwd)}"
TICKET=wq2271
WORKER=t1-gate-replay
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/te-t1-gate.XXXXXX")
trap 'kill "$DPID" "$FPID" 2>/dev/null; rm -rf "$ROOT"' EXIT

export TOKEN_ENGINE_STATE_DIR="$ROOT/state"
export TOKEN_ENGINE_RUNTIME_DIR="$ROOT/run"
export TOKEN_ENGINE_SOCK="$ROOT/run/broker.sock"
export TOKEN_ENGINE_CLIENT_GROUP="$(id -gn)"
export TOKEN_ENGINE_FORGES_PATH="$ROOT/forges.json"
export TOKEN_ENGINE_GRANT_DIR="$ROOT/events"
export FLEET_EXEC_AUDIT_PATH="$ROOT/events/fleet-exec.jsonl"
export PYTHONPATH="/home/lumen/bin${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$ROOT/state/secrets" "$ROOT/run" "$ROOT/events" "$ROOT/forge"

# The gate token: random, engine-store only. Deliberately NOT mask-shaped —
# the mask patterns are covered by unit tests; this replay proves placement.
TOKEN=$(python3 -c 'import uuid; print("t1gate" + uuid.uuid4().hex)')
printf '%s\n' "$TOKEN" > "$ROOT/state/secrets/gitea-lumen.token"
git init --bare --quiet "$ROOT/forge/lumen/proj.git"

echo "== 1. fake forge up (Basic auth enforced)"
python3 "$SRC/tests/fake_git_forge.py" --root "$ROOT/forge" --user lumen \
    --token-file "$ROOT/state/secrets/gitea-lumen.token" \
    > "$ROOT/forge.port" 2>"$ROOT/forge.log" &
FPID=$!
for _ in $(seq 1 50); do grep -q PORT= "$ROOT/forge.port" 2>/dev/null && break; sleep 0.1; done
PORT=$(sed -n 's/^PORT=//p' "$ROOT/forge.port")
[ -n "$PORT" ] || { echo "GATE FAIL: forge never bound"; cat "$ROOT/forge.log"; exit 1; }
python3 - "$ROOT/forges.json" "$PORT" <<'PYEOF'
import json, sys
json.dump({"gitea_url": "http://127.0.0.1:" + sys.argv[2],
           "gitea_owners": ["lumen"], "gitea_token_file": "gitea-lumen.token",
           "gitea_git_username": "lumen"}, open(sys.argv[1], "w"))
PYEOF

echo "== 2. env-scoped daemon up"
python3 "$SRC/token_engine_daemon.py" >>"$ROOT/daemon.log" 2>&1 &
DPID=$!
for _ in $(seq 1 50); do [ -S "$TOKEN_ENGINE_SOCK" ] && break; sleep 0.1; done
[ -S "$TOKEN_ENGINE_SOCK" ] || { echo "GATE FAIL: no socket"; cat "$ROOT/daemon.log"; exit 1; }

echo "== 3. client repo: userinfo-free remote + the fworker worktree config"
CLIENT="$ROOT/client"
git init --quiet -b main "$CLIENT"
git -C "$CLIENT" -c user.name=t1 -c user.email=t1@gate \
    commit --allow-empty --quiet -m "t1 gate commit"
git -C "$CLIENT" remote add origin "http://127.0.0.1:$PORT/lumen/proj.git"
# exactly what fworker.write_worktree_credential_config writes
git -C "$CLIENT" config extensions.worktreeConfig true
git -C "$CLIENT" config --worktree --replace-all credential.helper ""
git -C "$CLIENT" config --worktree --add credential.helper token-engine
git -C "$CLIENT" config --worktree credential.useHttpPath true
# helper shim on PATH, as /usr/local/bin provides in production
mkdir -p "$ROOT/bin"
cp "$SRC/git-credential-token-engine" "$ROOT/bin/git-credential-token-engine"
chmod 755 "$ROOT/bin/git-credential-token-engine"

echo "== 4. push through helper -> broker -> handoff"
if ! PATH="$ROOT/bin:$PATH" FOREMAN_TICKET_ID="$TICKET" FOREMAN_WORKER="$WORKER" \
     git -C "$CLIENT" push --quiet origin main 2>"$ROOT/push.log"; then
  echo "GATE FAIL: push failed"; cat "$ROOT/push.log" "$ROOT/daemon.log"; exit 1
fi
git -C "$ROOT/forge/lumen/proj.git" rev-parse --verify --quiet refs/heads/main >/dev/null \
  || { echo "GATE FAIL: branch never landed on the forge"; exit 1; }
echo "push succeeded; refs/heads/main is on the forge"

echo "== 5. no token anywhere a worker can read"
URL=$(git -C "$CLIENT" remote get-url origin)
case "$URL" in *@*) echo "GATE FAIL: remote URL carries userinfo: $URL"; exit 1;; esac
if grep -rF "$TOKEN" "$CLIENT" >/dev/null 2>&1; then
  echo "GATE FAIL: token found under the client repo"; exit 1
fi
if printenv | grep -qF "$TOKEN"; then
  echo "GATE FAIL: token in the environment"; exit 1
fi
SWEEP_SRC="$(dirname "$SRC")/ops/wq2243-remote-credential-sweep/remote_credential_sweep.py"
python3 "$SWEEP_SRC" scan --root "$ROOT/client" \
  || { echo "GATE FAIL: sweep found a credentialed remote"; exit 1; }
echo "git config, env, sweep: clean"

echo "== 6. one-shot handoff consumed"
LEFT=$(ls "$ROOT/run/minted" 2>/dev/null | grep -c ^ || true)
[ "${LEFT:-0}" = 0 ] || { echo "GATE FAIL: $LEFT handoff file(s) left behind"; exit 1; }

echo "GATE_REPLAY_T1_PASS"
