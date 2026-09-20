#!/usr/bin/env bash
# wq#2277 (T9) gate replay: "killing a worker mid-ticket leaves no live minted
# token." Runs an env-scoped daemon as the calling uid (no root, no systemd),
# against the test-file provider:
#
#   1. start daemon A            → broker socket up, reaper finds empty ledger
#   2. worker mints a token      → provider live-marker exists (token accepted)
#   3. kill -9 the worker        → SessionEnd never fires, no ticket-end sent
#   4. stop daemon A, start B    → §3.4 reaper sweeps the ledger
#   5. provider validation of the old token FAILS → gate holds
#   6. ticket-end for the swept ticket → had_entries=0 (idempotency)
#
#   bash token-engine/gate_replay_t9.sh [token-engine src dir]
set -uo pipefail

SRC="${1:-$(cd "$(dirname "$0")" && pwd)}"
TICKET=wq2277
WORKER=t9-gate-replay
ROOT=$(mktemp -d /tmp/te-t9-gate.XXXXXX)
trap 'kill "$DPID" 2>/dev/null; rm -rf "$ROOT"' EXIT

export TOKEN_ENGINE_STATE_DIR="$ROOT/state"
export TOKEN_ENGINE_RUNTIME_DIR="$ROOT/run"
export TOKEN_ENGINE_SOCK="$ROOT/run/broker.sock"
export TOKEN_ENGINE_CLIENT_GROUP="$(id -gn)"
export TOKEN_ENGINE_ALLOW_TEST_MINT=1
export FLEET_EXEC_AUDIT_PATH="$ROOT/events/fleet-exec.jsonl"
# In a repo checkout fleet_exec_policy.py sits in bin, not next to the daemon
# (install.sh snapshots it into /usr/local/lib/token-engine for production).
export PYTHONPATH="/home/lumen/bin${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$ROOT/state" "$ROOT/events"

python3 - "$ROOT" <<'PYEOF'
import hashlib, json, sys
grant = {"ticket": "wq2277", "expires": "2099-01-01T00:00:00Z",
         "grants": [{"kind": "token_engine", "node": "daemon"}]}
raw = json.dumps(grant).encode()
open(sys.argv[1] + "/grant.json", "wb").write(raw)
open(sys.argv[1] + "/grant.sha", "w").write(hashlib.sha256(raw).hexdigest())
PYEOF
SHA=$(cat "$ROOT/grant.sha")
CLIENT="python3 $SRC/te_client.py --ticket $TICKET --worker $WORKER \
        --grant-file $ROOT/grant.json --grant-sha $SHA"

start_daemon() {
  python3 "$SRC/token_engine_daemon.py" >>"$ROOT/daemon.log" 2>&1 &
  DPID=$!
  for _ in $(seq 1 50); do [ -S "$TOKEN_ENGINE_SOCK" ] && return 0; sleep 0.1; done
  echo "GATE FAIL: daemon never bound socket"; cat "$ROOT/daemon.log"; exit 1
}

echo "== 1. daemon A up"
start_daemon

echo "== 2. worker (a real process) mints a token"
sleep 300 & WPID=$!
OUT=$($CLIENT --verb mint-test) || { echo "GATE FAIL: mint denied: $OUT"; exit 1; }
echo "$OUT"
TOKEN_ID=$(echo "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["token_id"])')
LIVE="$ROOT/state/test-provider/$TOKEN_ID.live"
[ -f "$LIVE" ] || { echo "GATE FAIL: provider does not know the token"; exit 1; }
echo "provider accepts $TOKEN_ID (live marker present)"

echo "== 3. kill -9 the worker mid-ticket (SessionEnd never fires)"
kill -9 "$WPID"; wait "$WPID" 2>/dev/null
[ -f "$LIVE" ] || { echo "GATE FAIL: token died early?"; exit 1; }
echo "worker dead, token still live — this is the leak window"

echo "== 4. restart the daemon (A down, B up → §3.4 reaper)"
kill "$DPID"; wait "$DPID" 2>/dev/null
# A killed daemon leaves its socket file behind; remove it so start_daemon's
# socket-wait observes daemon B (which reaps BEFORE binding), not A's corpse.
rm -f "$TOKEN_ENGINE_SOCK"
start_daemon

echo "== 5. provider rejects the old token"
if [ -f "$LIVE" ]; then echo "GATE FAIL: token survived the reaper"; exit 1; fi
[ -e "$ROOT/state/minted/$TICKET.json" ] && { echo "GATE FAIL: ledger not cleared"; exit 1; }
grep -o 'reaper: {[^}]*}' "$ROOT/daemon.log" | tail -1
echo "provider validation of $TOKEN_ID: REJECTED (live marker revoked)"

echo "== 6. ticket-end after the sweep is a clean no-op (idempotency)"
OUT=$($CLIENT --verb ticket-end) || { echo "GATE FAIL: ticket-end errored: $OUT"; exit 1; }
echo "$OUT"
echo "$OUT" | grep -q '"had_entries": 0' || { echo "GATE FAIL: not idempotent"; exit 1; }

echo "GATE_REPLAY_T9_PASS"
