#!/usr/bin/env bash
# wq#3296 — runs ON NEITH (hourly timer). Pull-based replica, neith initiates:
#   1. ssh Lumen to refresh the staged snapshot (sqlite .backup there)
#   2. rsync the staging dir down to the local read-only mirror
#   3. restic backup of the mirror into the local repo, then prune
# Single writer rule: this box never writes back to Lumen; promotion is a
# manual runbook step (design wq#3261).
set -euo pipefail

LUMEN="${LUMEN_SSH:-lumen@100.117.66.102}"   # verify against lumen_state at install
ROOT=/srv/fleet-memory
MIRROR="$ROOT/current"
STATUS="$ROOT/status"
RESTIC_REPO="$ROOT/restic"
export RESTIC_PASSWORD_FILE="$ROOT/.restic-pass"

fail() { echo "{\"ok\": false, \"step\": \"$1\", \"at\": \"$(date -u +%FT%TZ)\"}" > "$STATUS/pull.json"; exit 1; }
mkdir -p "$MIRROR" "$STATUS"

ssh -o BatchMode=yes -o ConnectTimeout=15 "$LUMEN" \
  'bash ~/foreman/ops/wq3296-neith-bringup/lumen-export-memory-snapshot.sh' \
  || fail export

rsync -a --delete "$LUMEN:fleet-memory-staging/" "$MIRROR/" || fail rsync

test -s "$MIRROR/minerva.db" && test -s "$MIRROR/EXPORTED_AT" || fail sanity

restic -r "$RESTIC_REPO" backup --tag fleet-memory "$MIRROR" || fail restic
restic -r "$RESTIC_REPO" forget --tag fleet-memory \
  --keep-hourly 48 --keep-daily 30 --keep-weekly 12 --prune || fail prune

echo "{\"ok\": true, \"exported_at\": \"$(cat "$MIRROR/EXPORTED_AT")\", \"at\": \"$(date -u +%FT%TZ)\"}" \
  > "$STATUS/pull.json"
