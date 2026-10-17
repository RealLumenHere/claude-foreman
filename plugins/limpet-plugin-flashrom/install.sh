#!/bin/bash
set -euo pipefail
echo "[flashrom] Installing flashrom plugin..."
if ! command -v flashrom &>/dev/null; then
    echo "[flashrom] Installing flashrom..."
    sudo apt-get update -qq && sudo apt-get install -y -qq flashrom
fi
DEST=/opt/limpet/plugins/flashrom
sudo mkdir -p "$DEST/mcp"
sudo cp "$(dirname "$0")/mcp/server.py" "$DEST/mcp/"
sudo chmod +x "$DEST/mcp/server.py"
if [ -d /etc/udev/rules.d ]; then
    sudo cp "$(dirname "$0")/assets/90-limpet-flash-programmer.rules" /etc/udev/rules.d/ 2>/dev/null || true
    sudo udevadm control --reload-rules 2>/dev/null || true
fi
echo "[flashrom] Plugin ready."
