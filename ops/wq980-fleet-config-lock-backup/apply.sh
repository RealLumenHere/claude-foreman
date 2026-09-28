#!/bin/bash
# wq#980 — install fleet-safe-write to /home/lumen/bin.
# Idempotent: re-running just re-copies the (identical) script.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/lumen/bin/fleet-safe-write

"$HERE/../wq2856-atomic-install/install-script" -m 0755 "$HERE/fleet-safe-write.sh" "$DEST"
