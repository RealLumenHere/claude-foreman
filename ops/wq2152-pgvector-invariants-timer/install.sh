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
  # wq#5260: no venabled/vactive/vtimer_scheduled for lumen-chunk-doc-invariants.timer: it
  # is intended-off, disabled on purpose by the 2026-10-08 subtraction
  # (/home/lumen/scratch/subtraction/timers-disable-list.txt:28). wq#4910 dropped only the
  # scheduled check, so --verify still demanded enabled+active, failed forever, and the
  # ops-root-apply it filed told the reader to run this script as root, whose
  # `enable --now` would have undone the subtraction. Re-enabling it is a decision, not a
  # heal; whoever makes it restores the enable below and adds venabled/vactive/
  # vtimer_scheduled here.
  vdone
fi

python3 -m pytest -q "$SRC1776/test_check_chunk_doc_invariants.py"

install -m 0755 "$SRC1776/check_chunk_doc_invariants.py" /home/lumen/bin/check-pgvector-invariants
sudo install -m 0644 "$HERE/lumen-chunk-doc-invariants.service" /etc/systemd/system/lumen-chunk-doc-invariants.service
sudo install -m 0644 "$HERE/lumen-chunk-doc-invariants.timer" /etc/systemd/system/lumen-chunk-doc-invariants.timer
sudo systemctl daemon-reload
# wq#5260: intended-off (timers-disable-list.txt:28): unit files ship and are verified,
# but the timer is neither enabled nor started here.

# One live report (no --raise-aq: the timer owns state; this run persists nothing).
/home/lumen/bin/check-pgvector-invariants
