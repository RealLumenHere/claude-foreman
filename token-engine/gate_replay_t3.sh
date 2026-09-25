#!/usr/bin/env bash
# wq#2272 (T3) gate replay: "a crafted https://x:y@host/... push is refused;
# an ordinary push through the token-engine helper is unaffected; the hooks
# dir is not writable by uid lumen." Same env-scoped rig as gate_replay_t1.sh
# (loopback fake forge, no root, no network, no real credential), with
# core.hooksPath pointed at this branch's hooks dir:
#
#   1. hook unit: userinfo URL refused, message names wq#2262
#   2. ordinary push (fworker worktree config, token-engine helper) lands
#   3. stored-credential fallback: store helper holding a VALID credential —
#      connect succeeds, hook refuses, ref never lands
#   4. crafted userinfo remote holding the VALID token — refused end to end
#   5. installed hooks dir (when present) is not writable by this uid
#
#   bash token-engine/gate_replay_t3.sh [token-engine src dir]
set -uo pipefail

SRC="${1:-$(cd "$(dirname "$0")" && pwd)}"
TICKET=wq2272
WORKER=t3-gate-replay
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/te-t3-gate.XXXXXX")
DPID=""; FPID=""
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

HOOK="$SRC/hooks/pre-push"
chmod 755 "$HOOK"

echo "== 1. hook unit: userinfo URL refused, names wq#2262"
if "$HOOK" origin "https://x:y@github.com/lumen/proj.git" 2>"$ROOT/unit.err" </dev/null; then
  echo "GATE FAIL: userinfo URL was not refused"; exit 1
fi
grep -q "wq#2262" "$ROOT/unit.err" \
  || { echo "GATE FAIL: refusal does not name wq#2262"; cat "$ROOT/unit.err"; exit 1; }
echo "refused: $(head -1 "$ROOT/unit.err")"

TOKEN=$(python3 -c 'import uuid; print("t3gate" + uuid.uuid4().hex)')
printf '%s\n' "$TOKEN" > "$ROOT/state/secrets/gitea-lumen.token"
git init --bare --quiet "$ROOT/forge/lumen/proj.git"

echo "== 2a. fake forge up (Basic auth enforced)"
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

echo "== 2b. env-scoped daemon up"
python3 "$SRC/token_engine_daemon.py" >>"$ROOT/daemon.log" 2>&1 &
DPID=$!
for _ in $(seq 1 50); do [ -S "$TOKEN_ENGINE_SOCK" ] && break; sleep 0.1; done
[ -S "$TOKEN_ENGINE_SOCK" ] || { echo "GATE FAIL: no socket"; cat "$ROOT/daemon.log"; exit 1; }

echo "== 2c. ordinary push through the token-engine helper is unaffected"
CLIENT="$ROOT/client"
git init --quiet -b main "$CLIENT"
git -C "$CLIENT" -c user.name=t3 -c user.email=t3@gate \
    commit --allow-empty --quiet -m "t3 gate commit"
git -C "$CLIENT" remote add origin "http://127.0.0.1:$PORT/lumen/proj.git"
# exactly what fworker.write_worktree_credential_config writes (hooksPath
# aimed at this branch's hooks dir instead of the /usr/local install)
git -C "$CLIENT" config extensions.worktreeConfig true
git -C "$CLIENT" config --worktree --replace-all credential.helper ""
git -C "$CLIENT" config --worktree --add credential.helper token-engine
git -C "$CLIENT" config --worktree credential.useHttpPath true
git -C "$CLIENT" config --worktree core.hooksPath "$SRC/hooks"
mkdir -p "$ROOT/bin"
cp "$SRC/git-credential-token-engine" "$ROOT/bin/git-credential-token-engine"
chmod 755 "$ROOT/bin/git-credential-token-engine"
if ! PATH="$ROOT/bin:$PATH" FOREMAN_TICKET_ID="$TICKET" FOREMAN_WORKER="$WORKER" \
     git -C "$CLIENT" push --quiet origin main 2>"$ROOT/push.log"; then
  echo "GATE FAIL: ordinary push through the helper was blocked"
  cat "$ROOT/push.log" "$ROOT/daemon.log"; exit 1
fi
git -C "$ROOT/forge/lumen/proj.git" rev-parse --verify --quiet refs/heads/main >/dev/null \
  || { echo "GATE FAIL: branch never landed on the forge"; exit 1; }
echo "push succeeded through the hook; refs/heads/main is on the forge"

echo "== 3. stored-credential fallback refused despite a VALID credential"
git -C "$CLIENT" branch --quiet t3-fallback
# useHttpPath=true is in force, so the stored entry carries the repo path
printf 'http://lumen:%s@127.0.0.1:%s/lumen/proj.git\n' "$TOKEN" "$PORT" > "$ROOT/stored-creds"
git -C "$CLIENT" config --worktree --replace-all credential.helper ""
git -C "$CLIENT" config --worktree --add credential.helper "store --file=$ROOT/stored-creds"
if PATH="$ROOT/bin:$PATH" \
     git -C "$CLIENT" push --quiet origin t3-fallback 2>"$ROOT/fallback.log"; then
  echo "GATE FAIL: push rode the store helper past the hook"; exit 1
fi
grep -q "wq#2262" "$ROOT/fallback.log" \
  || { echo "GATE FAIL: fallback refusal does not name wq#2262"; cat "$ROOT/fallback.log"; exit 1; }
grep -q "token-engine" "$ROOT/fallback.log" \
  || { echo "GATE FAIL: refusal does not name the sanctioned helper"; cat "$ROOT/fallback.log"; exit 1; }
if git -C "$ROOT/forge/lumen/proj.git" rev-parse --verify --quiet refs/heads/t3-fallback >/dev/null; then
  echo "GATE FAIL: refused ref landed on the forge anyway"; exit 1
fi
echo "store-helper push refused; ref never landed"

echo "== 4. crafted userinfo remote (valid token embedded) refused end to end"
git -C "$CLIENT" branch --quiet t3-userinfo
git -C "$CLIENT" remote set-url origin "http://lumen:$TOKEN@127.0.0.1:$PORT/lumen/proj.git"
if git -C "$CLIENT" push --quiet origin t3-userinfo 2>"$ROOT/userinfo.log"; then
  echo "GATE FAIL: userinfo push went through"; exit 1
fi
grep -q "userinfo" "$ROOT/userinfo.log" && grep -q "wq#2262" "$ROOT/userinfo.log" \
  || { echo "GATE FAIL: not refused by the userinfo wall"; cat "$ROOT/userinfo.log"; exit 1; }
if git -C "$ROOT/forge/lumen/proj.git" rev-parse --verify --quiet refs/heads/t3-userinfo >/dev/null; then
  echo "GATE FAIL: userinfo ref landed on the forge anyway"; exit 1
fi
echo "userinfo push refused; ref never landed"

echo "== 5. installed hooks dir not writable by this uid"
LIB_HOOKS=/usr/local/lib/token-engine/hooks
if [ -d "$LIB_HOOKS" ]; then
  if [ -w "$LIB_HOOKS" ] || [ -w "$LIB_HOOKS/pre-push" ]; then
    echo "GATE FAIL: $LIB_HOOKS is writable by uid $(id -un)"; exit 1
  fi
  echo "$LIB_HOOKS: read-only for uid $(id -un)"
else
  grep -q 'install -d -m 0755 -o root -g root "$LIB/hooks"' "$SRC/install.sh" \
    || { echo "GATE FAIL: install.sh does not root-own the hooks dir"; exit 1; }
  echo "$LIB_HOOKS not installed on this node; install.sh root-owns it (checked)"
fi

echo "GATE_REPLAY_T3_PASS"
