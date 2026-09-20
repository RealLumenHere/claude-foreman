#!/usr/bin/env bash
# wq#2218 — install the masked-unit provenance advisory into /home/lumen/bin/unit-drift.
# staged/unit-drift is the live file plus the wq#2218 change; installing is one copy.
# Idempotent: safe to re-run. The retired_services.yaml entry ships with the repo, not here.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
BIN=/home/lumen/bin

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
# vcontains, not vfile: unit-drift is a shared file other bundles also edit (wq#1658), so
# byte-identity with a staged snapshot would flag their edits as drift.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vcontains "$BIN/unit-drift" "def unexplained_masks"
  vcontains "$BIN/unit-drift" "print_mask_advisory(masks)"
  vdone
fi

echo "== installing unit-drift with the masked-unit advisory =="
if cmp -s "$SRC/staged/unit-drift" "$BIN/unit-drift"; then
  echo "unchanged: $BIN/unit-drift"
else
  cp -p "$BIN/unit-drift" "$BIN/unit-drift.pre-wq2218"
  install -m 0755 "$SRC/staged/unit-drift" "$BIN/unit-drift"
  echo "installed: $BIN/unit-drift (previous copy: unit-drift.pre-wq2218)"
fi
