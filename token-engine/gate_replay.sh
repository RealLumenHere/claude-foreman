#!/usr/bin/env bash
# wq#2270 gate replay, run as uid lumen (fresh session so token-client group
# membership is live):
#
#   /home/lumen/bin/lumen-run start --name te-gate -- bash \
#       <repo>/token-engine/gate_replay.sh <repo>/token-engine
#
# Exercises: EACCES on the secret store, allow + deny + mask-tripwire calls
# through the broker, and the audit lines those calls appended.
set -uo pipefail

SRC="${1:-/home/lumen/foreman/token-engine}"
TICKET=wq2270
WORKER=te-gate-replay
AUDIT=/home/lumen/foreman/events/fleet-exec.jsonl

echo "== identity: $(id)"

echo "== 1. secret store unreadable by uid lumen"
if cat /var/lib/token-engine/secrets/.canary 2>&1; then
    echo "GATE FAIL: canary readable"; exit 1
fi

echo "== 2. daemon unit active"
systemctl is-active token-engine.service || { echo "GATE FAIL: unit inactive"; exit 1; }

echo "== 3. broker calls (allow / grant deny / mask tripwire)"
# The grant must sit where uid tokeneng can traverse and read it (world-readable
# per design §1.3); the daemon's PrivateTmp hides /tmp, so use the events tree.
GDIR=$(mktemp -d -p /home/lumen/foreman/events te-gate.XXXXXX)
chmod 755 "$GDIR"
python3 - "$GDIR" <<'PYEOF'
import hashlib, json, sys
grant = {"ticket": "wq2270", "expires": "2027-01-01T00:00:00Z",
         "grants": [{"kind": "token_engine", "node": "daemon"}]}
raw = json.dumps(grant).encode()
open(sys.argv[1] + "/grant.json", "wb").write(raw)
import os; os.chmod(sys.argv[1] + "/grant.json", 0o444)
open(sys.argv[1] + "/grant.sha", "w").write(hashlib.sha256(raw).hexdigest())
PYEOF
SHA=$(cat "$GDIR/grant.sha")
CLIENT="python3 $SRC/te_client.py --ticket $TICKET --worker $WORKER --grant-file $GDIR/grant.json"

echo "-- ping (expect ok=true)"
$CLIENT --grant-sha "$SHA" --verb ping || { echo "GATE FAIL: ping denied"; exit 1; }

echo "-- bad sha (expect deny grant_sha_mismatch)"
$CLIENT --grant-sha "$(printf '0%.0s' {1..64})" --verb ping && { echo "GATE FAIL: bad sha allowed"; exit 1; }

echo "-- mask-selftest (expect fail with rule=mask_tripwire, no leak in frame)"
OUT=$($CLIENT --grant-sha "$SHA" --verb mask-selftest)
echo "$OUT"
echo "$OUT" | grep -q '"rule": "mask_tripwire"' || { echo "GATE FAIL: tripwire did not fire"; exit 1; }
echo "$OUT" | grep -q 'ghp_selftest' && { echo "GATE FAIL: leak escaped in frame"; exit 1; }

echo "== 4. audit lines carrying the ticket id (global + per-worker sink)"
grep -c "\"ticket\": \"$TICKET\"" "$AUDIT" || { echo "GATE FAIL: no global audit line"; exit 1; }
tail -3 "$AUDIT"
grep -c "\"ticket\": \"$TICKET\"" "/home/lumen/foreman/events/$WORKER.jsonl" \
    || { echo "GATE FAIL: no per-worker audit line"; exit 1; }

rm -rf "$GDIR"
echo "GATE_REPLAY_PASS"
