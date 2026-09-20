#!/usr/bin/env bash
# wq#2270 gate replay (+ wq#2278 T10 checks), run as uid lumen (fresh session
# so token-client group membership is live):
#
#   /home/lumen/bin/lumen-run start --name te-gate -- bash \
#       <repo>/token-engine/gate_replay.sh <repo>/token-engine
#
# Exercises: EACCES on the secret store (incl. every migrated rank 5-8 file
# that exists), allow + deny + mask-tripwire calls through the broker, T10
# verb policy/grant denies, and the audit lines those calls appended.
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
# T10 (wq#2278) gate: every migrated rank 5-8 credential (design §7) that is in
# the store must be EACCES for uid lumen. A file not migrated yet is reported,
# not failed — install.sh's migrate step is per-host/operator-supplied.
for f in litellm-admin.token ebay.env cloudflare-api.token fleet-ssh/id_ed25519; do
    p="/var/lib/token-engine/secrets/$f"
    if cat "$p" 2>/dev/null; then
        echo "GATE FAIL: $p readable by uid lumen"; exit 1
    fi
    echo "unreadable-or-absent (both acceptable pre-cutover): $p"
done

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

echo "== 3b. T10 verbs (wq#2278): policy+grant deny paths, schema-clean frames"
# The T2 grant is token_engine/daemon only — every T10 verb must DENY on it
# (grant_no_match or a policy rule), and the frame must carry the documented
# keys with an empty result. A key value or Authorization header in any frame
# is a gate failure.
for spec in "gateway-key mint 24h 15" "api-call cloudflare GET /client/v4/user/tokens/verify" "plug p1 state" "ssh-exec thor uptime"; do
    set -- $spec
    VERB=$1; shift
    OUT=$($CLIENT --grant-sha "$SHA" --verb "$VERB" --argv "$@")
    echo "$OUT"
    if [ "$(echo "$OUT" | python3 -c 'import json,sys; f=json.load(sys.stdin); print(f["ok"], f["result"])')" != "False {}" ]; then
        echo "GATE FAIL: $VERB allowed on a token_engine/daemon grant"; exit 1
    fi
    echo "$OUT" | grep -Eq 'sk-[A-Za-z0-9_-]{16,}|Authorization' \
        && { echo "GATE FAIL: credential shape in $VERB frame"; exit 1; }
done

echo "== 4. audit lines carrying the ticket id (global + per-worker sink)"
grep -c "\"ticket\": \"$TICKET\"" "$AUDIT" || { echo "GATE FAIL: no global audit line"; exit 1; }
tail -3 "$AUDIT"
grep -c "\"ticket\": \"$TICKET\"" "/home/lumen/foreman/events/$WORKER.jsonl" \
    || { echo "GATE FAIL: no per-worker audit line"; exit 1; }

rm -rf "$GDIR"
echo "GATE_REPLAY_PASS"
