#!/bin/bash
# limpet-plugin-jlink-mcp install script
# wq#4140 — installs jlink-mcp from npm
set -euo pipefail

echo "[jlink-mcp] Installing jlink-mcp plugin..."

if ! command -v node &>/dev/null; then
    echo "[jlink-mcp] ERROR: Node.js 18+ is required and was not found" >&2
    exit 1
fi

echo "[jlink-mcp] Installing jlink-mcp from npm..."
sudo npm install -g jlink-mcp@0.7.0

if command -v jlink-mcp &>/dev/null; then
    echo "[jlink-mcp] Installation complete."
else
    echo "[jlink-mcp] ERROR: jlink-mcp not found after install" >&2
    exit 1
fi

if [ -d /etc/udev/rules.d ]; then
    echo "[jlink-mcp] Installing udev rules..."
    sudo cp "$(dirname "$0")/assets/90-limpet-jlink-mcp.rules" /etc/udev/rules.d/ 2>/dev/null || true
    sudo udevadm control --reload-rules 2>/dev/null || true
fi

echo "[jlink-mcp] Plugin ready."
echo "[jlink-mcp] Note: the native 'jlink' PROBE_TYPE also needs the SEGGER"
echo "[jlink-mcp] J-Link software (https://www.segger.com/downloads/jlink/)."
echo "[jlink-mcp] The 'openocd' PROBE_TYPE needs openocd; 'blackmagic' needs"
echo "[jlink-mcp] arm-none-eabi-gdb. Neither is installed by this script."
