#!/usr/bin/env bash
# Install lumen-chunk-doc-invariants timer (wq#2152): checker script (from the
# wq#1776 bundle, carrying the wq#1788 query-failure fix) -> /home/lumen/bin,
# units -> systemd. Idempotent: re-running converges to the same state.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SRC1776="$HERE/../wq1776-pgvector-chunk-doc-invariants"

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vfile      "$SRC1776/check_chunk_doc_invariants.py"    /home/lumen/bin/check-pgvector-invariants
  vunit_file "$HERE/lumen-chunk-doc-invariants.service"  /etc/systemd/system/lumen-chunk-doc-invariants.service
  vunit_file "$HERE/lumen-chunk-doc-invariants.timer"    /etc/systemd/system/lumen-chunk-doc-invariants.timer
  venabled   lumen-chunk-doc-invariants.timer
  vactive    lumen-chunk-doc-invariants.timer
  vdone
fi

python3 -m pytest -q "$SRC1776/test_check_chunk_doc_invariants.py"

install -m 0755 "$SRC1776/check_chunk_doc_invariants.py" /home/lumen/bin/check-pgvector-invariants
sudo install -m 0644 "$HERE/lumen-chunk-doc-invariants.service" /etc/systemd/system/lumen-chunk-doc-invariants.service
sudo install -m 0644 "$HERE/lumen-chunk-doc-invariants.timer" /etc/systemd/system/lumen-chunk-doc-invariants.timer
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-chunk-doc-invariants.timer
systemctl is-active --quiet lumen-chunk-doc-invariants.timer
systemctl list-timers lumen-chunk-doc-invariants.timer --no-pager --no-legend

# One live report (no --raise-aq: the timer owns state; this run persists nothing).
/home/lumen/bin/check-pgvector-invariants
