#!/bin/bash
# wq#959 — install the 3-tier instincts.yaml + tier-2 playbooks live.
# Idempotent: re-running converges to the same state; every overwrite is backed up.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TS="$(date +%Y%m%d_%H%M%S)"
LIVE=/home/lumen/instincts.yaml
PB_DIR=/home/lumen/instructions/playbooks

if [ -f "$LIVE" ] && ! grep -q '^schema: 2' "$LIVE"; then
  cp -a "$LIVE" "$LIVE.bak.${TS}_wq959_pre_tiering"
  echo "backed up: $LIVE.bak.${TS}_wq959_pre_tiering"
fi
cp "$HERE/instincts.yaml" "$LIVE"
echo "installed: $LIVE (schema 2)"

for pb in quest fleet-ops llm-jobs hardware-claims; do
  dst="$PB_DIR/$pb.md"
  if [ -f "$dst" ] && ! cmp -s "$HERE/playbooks/$pb.md" "$dst"; then
    cp -a "$dst" "$dst.bak.${TS}_wq959"
  fi
  cp "$HERE/playbooks/$pb.md" "$dst"
  echo "installed: $dst"
done

echo "--- validation ---"
python3 "$HERE/../../harness/instincts_render.py" --check
