#!/bin/bash
set -euo pipefail
echo "[hackrf-mcp] Uninstalling..."
sudo rm -rf /opt/limpet/plugins/hackrf-mcp 2>/dev/null || true
sudo rm -f /etc/udev/rules.d/90-limpet-hackrf-mcp.rules 2>/dev/null || true
sudo udevadm control --reload-rules 2>/dev/null || true
echo "[hackrf-mcp] Uninstalled."
