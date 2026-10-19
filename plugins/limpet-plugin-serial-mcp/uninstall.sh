#!/bin/bash
set -euo pipefail
echo "[serial-mcp] Uninstalling..."
sudo rm -rf /opt/limpet/plugins/serial-mcp 2>/dev/null || true
sudo rm -f /etc/udev/rules.d/90-limpet-serial-mcp.rules 2>/dev/null || true
sudo udevadm control --reload-rules 2>/dev/null || true
echo "[serial-mcp] Uninstalled."
