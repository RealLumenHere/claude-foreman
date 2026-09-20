#!/usr/bin/env bash
# wq#2135 — nothing to install: lumen_state.diff is a record of the _heal_null_ids edit already
# applied live to /home/lumen/minerva/lumen_state.py (no git there). This script exists for the
# wq#1696 deploy-verify contract: --verify proves the edit is still in the live module and that
# the live minerva.db is healed (id-default trigger present, no state_facts row with id NULL).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
MINERVA=/home/lumen/minerva
DB="$MINERVA/minerva.db"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vcontains "$MINERVA/lumen_state.py" "^def _heal_null_ids\(conn\):"
  vcontains "$MINERVA/lumen_state.py" "^    _heal_null_ids\(conn\)$"
  vcontains "$MINERVA/lumen_state.py" "trg_state_facts_id_default"
  if [ ! -r "$DB" ]; then
    echo "ERR db unreadable: $DB"; _V_ERR=1
  else
    trig="$(sqlite3 -readonly "$DB" "SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name='trg_state_facts_id_default'" 2>/dev/null || echo ERR)"
    nulls="$(sqlite3 -readonly "$DB" "SELECT count(*) FROM state_facts WHERE id IS NULL" 2>/dev/null || echo ERR)"
    if [ "$trig" = ERR ] || [ "$nulls" = ERR ]; then
      echo "ERR could not query $DB"; _V_ERR=1
    else
      [ "$trig" = 1 ] || _vmiss "trigger trg_state_facts_id_default absent in $DB"
      [ "$nulls" = 0 ] || _vmiss "$nulls state_facts row(s) with id NULL in $DB"
    fi
  fi
  vdone
fi

echo "wq#2135: no install step — the edit in $SRC/lumen_state.diff is already applied live (see README.md)."
