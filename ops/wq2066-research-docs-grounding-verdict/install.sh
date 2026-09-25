#!/usr/bin/env bash
# Install research-grounding-verdict (wq#2066): tool -> /home/lumen/bin.
# Idempotent. Does NOT touch the database and does NOT touch /home/lumen/minerva —
# README.md "Deploy" lists the ordered steps (migrate, patch indexer, backfill,
# reader filter) and which of them need a session with minerva reach.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
BIN="${BIN:-/home/lumen/bin}"
MINERVA="${MINERVA:-/home/lumen/minerva}"
QUERY="${QUERY:-$BIN/minerva-query}"

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"

  # wq#2554: the deploy order in README.md ("1 before 2") is load-bearing — the
  # patched indexer writes both columns unconditionally, so a tree carrying step 2
  # without step 1 crashes the daily index run on UndefinedColumn, after the
  # embeddings are paid for and before the wq#309 chunk pass. A file-only verify
  # reports that state as live, so check the DB half too.
  vdbcol() {
    local col="$1" out
    out="$("$QUERY" sql "select 1 from information_schema.columns where table_name='research_docs' and column_name='$col'" 2>/dev/null)" \
      || { echo "ERR minerva-query failed: research_docs.$col"; _V_ERR=1; return; }
    case "$out" in
      *"(1 row"*) ;;
      *) _vmiss "db column absent: research_docs.$col (run: research-grounding-verdict migrate)" ;;
    esac
  }

  vfile     "$SRC/grounding_verdict.py" "$BIN/research-grounding-verdict"
  vfile     "$SRC/grounding_verdict.py" "$MINERVA/retrieval/grounding_verdict.py"
  vcontains "$MINERVA/retrieval/index_research.py" "from grounding_verdict import parse_header"
  vcontains "$MINERVA/retrieval/index_research.py" "grounding_verdict=EXCLUDED.grounding_verdict"
  vdbcol grounding_verdict
  vdbcol asserts_fleet_state
  vdone
fi

python3 -m pytest -q "$SRC/test_grounding_verdict.py"

install -m 0755 "$SRC/grounding_verdict.py" "$BIN/research-grounding-verdict"
