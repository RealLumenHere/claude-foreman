#!/bin/bash
# limpet-plugin-remote-cli install script
# wq#4396 — installs this plugin's MCP wrapper over wq#4395's remote-cli.
# remote-cli itself (the shared-PTY daemon + its /run/remote-cli/shared.sock)
# is installed fleet-wide by wq#4395, not by this script.
set -euo pipefail

DEST=/opt/limpet/plugins/remote-cli

echo "[remote-cli] Installing remote-cli plugin..."

if ! command -v remote-cli &>/dev/null; then
    echo "[remote-cli] WARNING: remote-cli binary not found on PATH." >&2
    echo "[remote-cli] WARNING: install it fleet-wide first (wq#4395)." >&2
fi

sudo mkdir -p "$DEST/mcp"
sudo cp "$(dirname "$0")/mcp/server.py" "$DEST/mcp/"
sudo chmod +x "$DEST/mcp/server.py"

echo "[remote-cli] Plugin ready."
