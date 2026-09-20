#!/usr/bin/env bash
# wq#2278 (T10) gate replay: the remaining verbs keep every credential inside
# the engine. Env-scoped daemon as the calling uid, a loopback fake LiteLLM:
#
#   1. gateway-key mint  → frame carries id/expiry/path, NEVER the value;
#                          the value lands only in the 0640 handoff file
#   2. audit             → one line per call in the dual sink
#   3. ticket-end        → /key/delete reaches the gateway, handoff file gone
#   4. plug / ssh-run    → deny paths hold without any credential in play
#
#   bash token-engine/gate_replay_t10.sh [token-engine src dir]
# The production half of the gate (uid lumen EACCES on the migrated rank 5-8
# files) is replayed by install.sh, same split as T2.
set -uo pipefail

SRC="${1:-$(cd "$(dirname "$0")" && pwd)}"
TICKET=wq2278
WORKER=t10-gate-replay
ROOT=$(mktemp -d /tmp/te-t10-gate.XXXXXX)
trap 'kill "$DPID" "$GWPID" 2>/dev/null; rm -rf "$ROOT"' EXIT

export TOKEN_ENGINE_STATE_DIR="$ROOT/state"
export TOKEN_ENGINE_RUNTIME_DIR="$ROOT/run"
export TOKEN_ENGINE_SOCK="$ROOT/run/broker.sock"
export TOKEN_ENGINE_CLIENT_GROUP="$(id -gn)"
export TOKEN_ENGINE_PROVIDERS_PATH="$ROOT/providers.json"
export FLEET_EXEC_AUDIT_PATH="$ROOT/events/fleet-exec.jsonl"
export FLEET_EXEC_POLICY_PATH="$ROOT/policy.yaml"
export PYTHONPATH="/home/lumen/bin${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$ROOT/state/secrets" "$ROOT/events"
printf 'never:\n  nodes: []\nrun_readonly_cmds:\n  - uptime\n' > "$ROOT/policy.yaml"
printf 'replay-admin-value\n' > "$ROOT/state/secrets/litellm-admin.token"

echo "== 0. fake gateway on loopback"
python3 "$SRC/tests/fake_gateway.py" "$ROOT/gw.json" &
GWPID=$!
for _ in $(seq 1 50); do [ -s "$ROOT/gw.json" ] && break; sleep 0.1; done
PORT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["port"])' "$ROOT/gw.json")
python3 - "$ROOT" "$PORT" <<'PYEOF'
import json, sys
root, port = sys.argv[1], int(sys.argv[2])
json.dump({"gateway": {"base_url": f"http://127.0.0.1:{port}",
                       "admin_key_file": "litellm-admin.token",
                       "max_budget_usd": 25.0, "max_ttl_s": 259200},
           "providers": {}, "plug_creds_file": "tapo.env",
           "plugs": {"p9": "192.0.2.9"}},
          open(root + "/providers.json", "w"))
grant = {"ticket": "wq2278", "expires": "2099-01-01T00:00:00Z",
         "grants": [{"kind": "token_engine", "node": "daemon"},
                    {"kind": "gateway_admin", "node": "gateway:litellm"},
                    {"kind": "ssh", "node": "bes", "verbs": ["copy"]},
                    {"kind": "plug", "node": "plug:p1"}]}
raw = json.dumps(grant).encode()
open(root + "/grant.json", "wb").write(raw)
import hashlib
open(root + "/grant.sha", "w").write(hashlib.sha256(raw).hexdigest())
PYEOF
SHA=$(cat "$ROOT/grant.sha")
CLIENT="python3 $SRC/te_client.py --ticket $TICKET --worker $WORKER \
        --grant-file $ROOT/grant.json --grant-sha $SHA"

echo "== 1. daemon up"
python3 "$SRC/token_engine_daemon.py" >>"$ROOT/daemon.log" 2>&1 &
DPID=$!
for _ in $(seq 1 50); do [ -S "$TOKEN_ENGINE_SOCK" ] && break; sleep 0.1; done
[ -S "$TOKEN_ENGINE_SOCK" ] || { echo "GATE FAIL: no socket"; cat "$ROOT/daemon.log"; exit 1; }

echo "== 2. gateway-key mint: frame has id+expiry+path, never the value"
OUT=$($CLIENT --verb gateway-key --argv mint worker 24h 15.0) \
    || { echo "GATE FAIL: mint denied: $OUT"; exit 1; }
echo "$OUT"
echo "$OUT" | grep -q '"key_id": "gwk-' || { echo "GATE FAIL: no key id"; exit 1; }
KEYFILE=$(echo "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["key_file"])')
VALPREFIX=$(head -c 3 "$KEYFILE")
[ "$VALPREFIX" = "sk-" ] || { echo "GATE FAIL: handoff file carries no key"; exit 1; }
VAL=$(cat "$KEYFILE")
case "$OUT" in *"$VAL"*) echo "GATE FAIL: key value leaked into the frame"; exit 1;; esac
PERMS=$(stat -c %a "$KEYFILE")
[ "$PERMS" = 640 ] || { echo "GATE FAIL: handoff perms $PERMS != 640"; exit 1; }
echo "frame is value-free; handoff file present, 0640"

echo "== 3. audit line per call, dual sink"
for f in "$ROOT/events/fleet-exec.jsonl" "$ROOT/events/$WORKER.jsonl"; do
    grep -q '"verb": "gateway-key"' "$f" \
        || { echo "GATE FAIL: no gateway-key audit line in $f"; exit 1; }
done
echo "audit lines present in both sinks"

echo "== 4. ticket-end revokes: /key/delete reaches the gateway"
OUT=$($CLIENT --verb ticket-end) || { echo "GATE FAIL: ticket-end: $OUT"; exit 1; }
echo "$OUT"
echo "$OUT" | grep -q '"revoked": 1' || { echo "GATE FAIL: nothing revoked"; exit 1; }
grep -q '"path": "/key/delete", "auth_present": true, "key_in_body": true' "$ROOT/gw.json.log" \
    || { echo "GATE FAIL: gateway never saw the delete"; cat "$ROOT/gw.json.log"; exit 1; }
[ -f "$KEYFILE" ] && { echo "GATE FAIL: handoff file survived revocation"; exit 1; }
echo "key deleted at the gateway, handoff file unlinked"

echo "== 5. deny paths hold: plug outside grant, ssh without run verb"
OUT=$($CLIENT --verb plug --argv p9 on) && { echo "GATE FAIL: plug allowed"; exit 1; }
echo "$OUT" | grep -q 'grant_no_match' || { echo "GATE FAIL: wrong plug deny: $OUT"; exit 1; }
OUT=$($CLIENT --verb ssh-run --argv bes uptime) && { echo "GATE FAIL: ssh allowed"; exit 1; }
echo "$OUT" | grep -q 'no_ssh_run' || { echo "GATE FAIL: wrong ssh deny: $OUT"; exit 1; }
echo "deny rules intact"

echo "T10 GATE: PASS"
