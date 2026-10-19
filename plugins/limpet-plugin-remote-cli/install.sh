#!/bin/bash
# limpet-plugin-remote-cli install script
# wq#4419 — installs this plugin's MCP wrapper over shared-terminal (lumen-bin
# PR #262). shared-terminal itself (the tmux-backed CLI) is installed
# fleet-wide from lumen-bin, not by this script.
set -euo pipefail

DEST=/opt/limpet/plugins/remote-cli

echo "[shared-terminal] Installing shared-terminal plugin..."

if [ ! -x /home/lumen/bin/shared-terminal ] && ! command -v shared-terminal &>/dev/null; then
    echo "[shared-terminal] WARNING: shared-terminal CLI not found." >&2
    echo "[shared-terminal] WARNING: install it from lumen-bin first (PR #262)." >&2
fi

if ! command -v tmux &>/dev/null; then
    echo "[shared-terminal] WARNING: tmux not installed (apt install tmux)." >&2
fi

sudo mkdir -p "$DEST/mcp"
sudo cp "$(dirname "$0")/mcp/server.py" "$DEST/mcp/"
sudo chmod +x "$DEST/mcp/server.py"

echo "[shared-terminal] Plugin ready."
