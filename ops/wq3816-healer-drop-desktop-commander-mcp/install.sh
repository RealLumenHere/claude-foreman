#!/usr/bin/env bash
# wq#3816 — drop the masked desktop-commander-mcp row from the healer watch table.
# Live file: /home/lumen/agent/healer_watch_table.yaml (outside git; healer reloads on mtime).
# healer_watch_table.diff is the record of the edit. Idempotent: safe to re-run.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
TABLE="${HEALER_WATCH_TABLE:-/home/lumen/agent/healer_watch_table.yaml}"  # override: tests only
ROW='^[[:space:]]+desktop-commander-mcp:'

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
# The change is a deletion, so "live" means the row is absent and the table still parses.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  if [ ! -r "$TABLE" ]; then
    echo "ERR target unreadable: $TABLE"; _V_ERR=1
  else
    grep -qE -- "$ROW" "$TABLE" && _vmiss "row still present: desktop-commander-mcp in $TABLE"
    python3 -c 'import sys,yaml; u=yaml.safe_load(open(sys.argv[1]))["units"]; sys.exit(0 if isinstance(u,dict) and u else 1)' "$TABLE" \
      || _vmiss "table does not parse to a non-empty units map: $TABLE"
  fi
  vdone
fi

echo "== dropping desktop-commander-mcp from the healer watch table =="
if grep -qE -- "$ROW" "$TABLE"; then
  cp -p "$TABLE" "$TABLE.pre-wq3816"
  sed -i -E "/$ROW/d" "$TABLE"
  echo "removed row from $TABLE (previous copy: $TABLE.pre-wq3816)"
else
  echo "unchanged: $TABLE"
fi
