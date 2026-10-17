#!/bin/bash
set -euo pipefail
echo "[mcjtag] Uninstalling..."
pip uninstall -y mcjtag 2>/dev/null || true
sudo rm -rf /opt/limpet/plugins/mcjtag 2>/dev/null || true
sudo rm -f /etc/udev/rules.d/91-limpet-jtag-swd-mcjtag.rules 2>/dev/null || true
sudo udevadm control --reload-rules 2>/dev/null || true
echo "[mcjtag] Uninstalled."
