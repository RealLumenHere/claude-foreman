#!/bin/bash
set -euo pipefail
echo "[buspirate-mcp] Uninstalling..."
sudo rm -rf /opt/limpet/plugins/buspirate-mcp 2>/dev/null || true
sudo rm -f /etc/udev/rules.d/90-limpet-buspirate-mcp.rules 2>/dev/null || true
sudo udevadm control --reload-rules 2>/dev/null || true
echo "[buspirate-mcp] Uninstalled."
