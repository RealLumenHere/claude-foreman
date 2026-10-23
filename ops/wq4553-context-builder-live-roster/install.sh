#!/usr/bin/env bash
# wq#4553 (wq#4539 phase 1): one render — context_builder.py roster+episodes
# from minerva.db, decisions from PG non-superseded, hand-prose preserved.
# Installs into minerva/memory (untracked there, so no repo to land it in —
# same wall as wq#4537). Run as lumen from a session with minerva write
# access: BUILD workers are walled out of /home/lumen/minerva (guard.sh,
# wq#1719/wq#900).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
LIVE=/home/lumen/minerva/memory/context_builder.py
BACKUP_DIR=/home/lumen/backups

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/context_builder.py" "$LIVE"
  vdone
fi

# Fresh dump before touching the renderer, even though this change is
# read-only on PG (ticket wq#4553 instruction; recipe_store.py:405 convention).
# A bare `pg_dump -Fc fleet_memory` defaults to the local unix socket on port 5432,
# which doesn't exist here — fleet_memory only listens on recipe_store.py's PG_HOST/
# PG_PORT (127.0.0.1:5433), so this calls recipe_store.backup() for the same
# -h/-p/-U/PGPASSWORD dump recipe_crystallizer already relies on (wq#4570).
mkdir -p "$BACKUP_DIR"
python3 -c "
import sys
sys.path.insert(0, '/home/lumen/foreman/harness')
from recipe_store import backup
backup('$BACKUP_DIR')
"

cp -p "$LIVE" "$LIVE.bak.$(date +%Y%m%d)_wq4553"
install -m 0644 "$SRC/context_builder.py" "$LIVE"
python3 "$LIVE" --out /home/lumen/claude_memory.md
bash "$0" --verify
