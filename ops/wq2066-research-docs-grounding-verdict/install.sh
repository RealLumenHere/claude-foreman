#!/usr/bin/env bash
# Install research-grounding-verdict (wq#2066): tool -> /home/lumen/bin.
# Idempotent. Does NOT touch the database and does NOT touch /home/lumen/minerva —
# README.md "Deploy" lists the ordered steps (migrate, patch indexer, backfill,
# reader filter) and which of them need a session with minerva reach.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

python3 -m pytest -q "$HERE/test_grounding_verdict.py"

install -m 0755 "$HERE/grounding_verdict.py" /home/lumen/bin/research-grounding-verdict
