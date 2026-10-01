#!/usr/bin/env bash
# wq#3296 — runs ON NEITH (weekly timer). VERIFIED restore test, not a repo
# check: restores the latest snapshot to a tmpdir, integrity-checks both
# sqlite DBs, and verifies the required file set. A backup that cannot
# restore is not a backup (design wq#3261: "weekly verified restore test").
set -euo pipefail

ROOT=/srv/fleet-memory
STATUS="$ROOT/status"
RESTIC_REPO="$ROOT/restic"
export RESTIC_PASSWORD_FILE="$ROOT/.restic-pass"
REQUIRED="minerva.db queue.db claude_memory.md HANDOFF.md instincts.yaml EXPORTED_AT"

TMP=$(mktemp -d /tmp/wq3296-restore-test.XXXXXX)
trap 'rm -rf "$TMP"' EXIT
fail() {
  echo "{\"ok\": false, \"step\": \"$1\", \"at\": \"$(date -u +%FT%TZ)\"}" > "$STATUS/restore-test.json"
  logger -t wq3296-restore-test "FAIL at step $1"
  exit 1
}
mkdir -p "$STATUS"

restic -r "$RESTIC_REPO" restore latest --tag fleet-memory --target "$TMP" || fail restore

RESTORED=$(find "$TMP" -name EXPORTED_AT -printf '%h\n' | head -1)
test -n "$RESTORED" || fail locate

echo "$REQUIRED" | tr ' ' '\n' | xargs -I{} test -s "$RESTORED/{}" || fail fileset

sqlite3 "$RESTORED/minerva.db" 'PRAGMA integrity_check;' | grep -qx ok || fail minerva-integrity
sqlite3 "$RESTORED/queue.db"   'PRAGMA integrity_check;' | grep -qx ok || fail queue-integrity

echo "{\"ok\": true, \"snapshot_exported_at\": \"$(cat "$RESTORED/EXPORTED_AT")\", \"at\": \"$(date -u +%FT%TZ)\"}" \
  > "$STATUS/restore-test.json"
logger -t wq3296-restore-test "OK, restored snapshot from $(cat "$RESTORED/EXPORTED_AT")"
