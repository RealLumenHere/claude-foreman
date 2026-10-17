#!/bin/bash
set -euo pipefail
echo "[gnuradio-mcp] Uninstalling..."
sudo python3 -m pip uninstall -y gnuradio-mcp-server 2>/dev/null || true
sudo rm -f /etc/udev/rules.d/90-limpet-gnuradio-mcp.rules 2>/dev/null || true
sudo udevadm control --reload-rules 2>/dev/null || true
echo "[gnuradio-mcp] Uninstalled. (GNU Radio system packages left in place.)"
