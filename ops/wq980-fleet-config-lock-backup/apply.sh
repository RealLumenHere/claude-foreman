#!/bin/bash
# wq#980 — install fleet-safe-write to /home/lumen/bin.
# Idempotent: re-running just re-copies the (identical) script.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/lumen/bin/fleet-safe-write

cp "$HERE/fleet-safe-write.sh" "$DEST"
chmod 755 "$DEST"
echo "installed: $DEST"
