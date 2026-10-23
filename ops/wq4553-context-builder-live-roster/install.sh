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
mkdir -p "$BACKUP_DIR"
pg_dump -Fc fleet_memory \
  > "$BACKUP_DIR/fleet_memory_pre-wq4553p1_$(date +%Y%m%d_%H%M%S).dump"

cp -p "$LIVE" "$LIVE.bak.$(date +%Y%m%d)_wq4553"
install -m 0644 "$SRC/context_builder.py" "$LIVE"
python3 "$LIVE" --out /home/lumen/claude_memory.md
bash "$0" --verify
