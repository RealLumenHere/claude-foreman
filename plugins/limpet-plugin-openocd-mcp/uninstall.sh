#!/bin/bash
set -euo pipefail
echo "[openocd-mcp] Uninstalling..."
pip uninstall -y openocd-mcp 2>/dev/null || true
sudo rm -f /etc/udev/rules.d/90-limpet-jtag-swd.rules 2>/dev/null || true
sudo udevadm control --reload-rules 2>/dev/null || true
echo "[openocd-mcp] Uninstalled."
