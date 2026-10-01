#!/usr/bin/env bash
# wq#3332 — runs ON NEPHTHYS (hourly user timer). Pull-based, nephthys initiates:
#   1. rsync bin/chat-export's archive down from Lumen (read-only against Lumen)
#   2. run chat-digest-local against the local mirror, using nephthys's own Ollama
#   3. write the digest + a status record locally
# Single writer rule: this box never writes back to Lumen, same shape as
# ops/wq3296-neith-bringup/neith-memory-pull.sh. Do NOT adapt this script for hathor — hathor's
# 2 ollama slots are dedicated to the judge (wq#2796); see README.md.
set -euo pipefail

LUMEN="${LUMEN_SSH:-lumen@100.117.66.102}"   # verify against lumen_state.py current lumen at install
ROOT="$HOME/chat-digest-local"
MIRROR="$ROOT/archive.json"
STATUS="$ROOT/status"
BIN="$HOME/bin/chat-digest-local"

fail() { echo "{\"ok\": false, \"step\": \"$1\", \"at\": \"$(date -u +%FT%TZ)\"}" > "$STATUS/pull.json"; exit 1; }
mkdir -p "$STATUS"

rsync -a "$LUMEN:.chat-archive/archive.json" "$MIRROR" || fail rsync
test -s "$MIRROR" || fail sanity

"$BIN" --archive "$MIRROR" --model qwen3:14b > "$ROOT/digest.json" || fail digest

echo "{\"ok\": true, \"pulled_at\": \"$(date -u +%FT%TZ)\", \"digest\": \"$ROOT/digest.json\"}" \
  > "$STATUS/pull.json"
