#!/usr/bin/env bash
# apply.sh — deploy lumen-kb-mcp from this ops dir to the host (wq#1151 P1).
# Run from a PRIVILEGED session (uses sudo for the unit install); fworker lanes
# cannot run this. Idempotent; backs up before overwriting.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DST=/home/lumen/projects/lumen-kb-mcp
UNIT=lumen-kb-mcp.service

mkdir -p "$DST"
for f in server.py kb_paths.py kb_distill.py requirements.txt; do
  [ -f "$DST/$f" ] && cp "$DST/$f" "$DST/$f.bak.$(date +%Y%m%d_%H%M%S)"
  install -m 0644 "$SRC/$f" "$DST/$f"
done

if [ ! -d "$DST/.venv" ]; then python3 -m venv "$DST/.venv"; fi
"$DST/.venv/bin/pip" install -q -r "$DST/requirements.txt"

# Unit: install only if changed; never stop/disable here.
if ! cmp -s "$SRC/$UNIT" "/etc/systemd/system/$UNIT" 2>/dev/null; then
  sudo install -m 0644 "$SRC/$UNIT" "/etc/systemd/system/$UNIT"
  sudo systemctl daemon-reload
fi
sudo systemctl enable --now "$UNIT"
sudo systemctl restart "$UNIT"

# Verify: MCP initialize handshake on loopback.
sleep 2
curl -sf -X POST http://127.0.0.1:8781/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"apply-check","version":"0"}}}' \
  | head -c 400 && echo
echo "OK: lumen-kb-mcp deployed and answering. Now: systemd_desired_state.py capture"
