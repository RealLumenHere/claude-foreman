#!/bin/bash
set -euo pipefail
echo "[flashrom] Uninstalling plugin..."
sudo rm -rf /opt/limpet/plugins/flashrom
sudo rm -f /etc/udev/rules.d/90-limpet-flash-programmer.rules 2>/dev/null || true
sudo udevadm control --reload-rules 2>/dev/null || true
echo "[flashrom] Uninstalled."
