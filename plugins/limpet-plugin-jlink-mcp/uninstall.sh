#!/bin/bash
set -euo pipefail
echo "[jlink-mcp] Uninstalling..."
sudo npm uninstall -g jlink-mcp 2>/dev/null || true
sudo rm -f /etc/udev/rules.d/90-limpet-jlink-mcp.rules 2>/dev/null || true
sudo udevadm control --reload-rules 2>/dev/null || true
echo "[jlink-mcp] Uninstalled."
