#!/usr/bin/env bash
# wq#3296 — runs ON LUMEN, invoked by neith over ssh at the start of each pull.
# Snapshots the fleet-memory data set into $STAGING. Sqlite files go through
# `.backup` (never a live copy — design wq#3261); plain files are copied with
# rsync so mtimes carry over. Idempotent; safe to run while writers are active
# (both DBs are WAL, wq#2907).
set -euo pipefail

STAGING="${FLEET_MEMORY_STAGING:-$HOME/fleet-memory-staging}"
mkdir -p "$STAGING"

sqlite3 /home/lumen/minerva/minerva.db ".backup '$STAGING/minerva.db'"
sqlite3 /home/lumen/agents/work-queue/queue.db ".backup '$STAGING/queue.db'"

# claude_memory.md plus any archive siblings (claude_memory_archive.md etc.);
# glob is deliberate — verify the archive filename at install (README).
rsync -a --delete-missing-args \
  /home/lumen/claude_memory* \
  /home/lumen/HANDOFF.md \
  /home/lumen/instincts.yaml \
  "$STAGING/"

date -u +%Y-%m-%dT%H:%M:%SZ > "$STAGING/EXPORTED_AT"
echo "export ok: $(ls "$STAGING" | grep -c .) entries in $STAGING"
