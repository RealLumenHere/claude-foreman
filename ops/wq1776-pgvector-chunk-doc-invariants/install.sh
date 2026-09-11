#!/usr/bin/env bash
# Install check-pgvector-invariants (wq#1776): script -> /home/lumen/bin.
# Idempotent: re-running converges to the same state. No timer installed —
# see README.md "both counts reported..." for the handoff/scan wiring steps.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

python3 -m pytest -q "$HERE/test_check_chunk_doc_invariants.py"

install -m 0755 "$HERE/check_chunk_doc_invariants.py" /home/lumen/bin/check-pgvector-invariants

/home/lumen/bin/check-pgvector-invariants
