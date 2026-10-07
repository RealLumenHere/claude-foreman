#!/usr/bin/env bash
# wq#3471 — install the fixed tbl_sweep2.py that probe-dead-references runs.
# /home/lumen/audits is not a git repo; this bundle is the tracked copy. Idempotent.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DST=/home/lumen/audits/tools/tbl_sweep2.py

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/tbl_sweep2.py" "$DST"
  vdone
fi

if ! cmp -s "$SRC/tbl_sweep2.py" "$DST"; then
  cp -p "$DST" "$DST.bak.$(date -u +%Y%m%dT%H%M%SZ)_pre_wq3471"
fi
"$SRC/../wq2856-atomic-install/install-script" -m 0644 "$SRC/tbl_sweep2.py" "$DST"
echo "installed $DST"
