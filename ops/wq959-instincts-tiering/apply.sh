#!/bin/bash
# wq#959 — install the 3-tier instincts.yaml + tier-2 playbooks live.
# Idempotent: re-running converges to the same state.
#
# wq#980: writes route through fleet-safe-write, which locks the target file
# (so a concurrent lane, e.g. a manual live edit, queues instead of racing)
# and takes an unconditional pre-write backup (so even a converged/idempotent
# re-run that overwrites with identical content leaves the prior state
# reconstructable, per change-safety.md G3).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LIVE=/home/lumen/instincts.yaml
PB_DIR=/home/lumen/instructions/playbooks
SAFE_WRITE=/home/lumen/bin/fleet-safe-write

"$SAFE_WRITE" "$LIVE" wq959_tiering --copy "$HERE/instincts.yaml"
echo "installed: $LIVE (schema 2)"

for pb in quest fleet-ops llm-jobs hardware-claims; do
  dst="$PB_DIR/$pb.md"
  "$SAFE_WRITE" "$dst" wq959 --copy "$HERE/playbooks/$pb.md"
  echo "installed: $dst"
done

echo "--- validation ---"
python3 "$HERE/../../harness/instincts_render.py" --check
