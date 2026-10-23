#!/usr/bin/env bash
# wq#4554 (wq#4539 phase 2): migrate PG fleet_decisions (+ fleet_facts) into
# minerva.db, point context_builder.py's decisions/facts sections at it, then
# freeze the 4 PG memory tables (rename to retired_*). recipes untouched.
#
# Installs into minerva/memory (untracked there, same wall as wq#4537/wq#4553):
# BUILD workers are walled out of /home/lumen/minerva (guard.sh, wq#1719/wq#900).
# Run as lumen from a session with minerva write access, or via
# `/home/lumen/bin/lumen-run` (wq#4570 precedent) from this sandbox.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
LIVE=/home/lumen/minerva/memory/context_builder.py
MINERVA_DB=/home/lumen/minerva/minerva.db
BACKUP_DIR=/home/lumen/backups

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/context_builder.py" "$LIVE"
  vdone
fi

# pg_dump -Fc fleet_memory before any schema change (recipe_store.py:405
# convention) — reuses recipe_store.backup() for the right host/port (wq#4570:
# a bare pg_dump defaults to the local unix socket, which doesn't exist here).
mkdir -p "$BACKUP_DIR"
python3 -c "
import sys
sys.path.insert(0, '/home/lumen/foreman/harness')
from recipe_store import backup
print(backup('$BACKUP_DIR'))
"

# Copy PG fleet_decisions + fleet_facts into minerva.db, audited via
# memory_audit (migrate_decisions.py). Idempotent — safe to re-run.
python3 "$SRC/migrate_decisions.py" --minerva-db "$MINERVA_DB"

cp -p "$LIVE" "$LIVE.bak.$(date +%Y%m%d)_wq4554"
install -m 0644 "$SRC/context_builder.py" "$LIVE"
python3 "$LIVE" --out /home/lumen/claude_memory.md

# Freeze the 4 PG memory tables now that the renderer no longer reads them.
python3 -c "
import sys
sys.path.insert(0, '/home/lumen/foreman/harness')
import recipe_store as rs
conn = rs._connect()
try:
    with conn, conn.cursor() as cur:
        with open('$SRC/freeze_pg_memory_tables.sql', encoding='utf-8') as fh:
            cur.execute(fh.read())
finally:
    conn.close()
print('froze 4 PG memory tables (renamed retired_*)')
"

bash "$0" --verify
