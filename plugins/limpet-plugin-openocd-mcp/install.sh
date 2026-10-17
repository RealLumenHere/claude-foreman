#!/bin/bash
# limpet-plugin-openocd-mcp install script
# wq#4137 — installs openocd-mcp from PyPI + ensures OpenOCD is present
set -euo pipefail

echo "[openocd-mcp] Installing openocd-mcp plugin..."

# Ensure OpenOCD is installed (system package)
if ! command -v openocd &>/dev/null; then
    echo "[openocd-mcp] Installing OpenOCD..."
    sudo apt-get update -qq && sudo apt-get install -y -qq openocd
fi

# Install openocd-mcp from PyPI
echo "[openocd-mcp] Installing openocd-mcp from PyPI..."
pip install --break-system-packages openocd-mcp==0.4.0

# Verify installation
if command -v openocd-mcp &>/dev/null || python3 -m openocd_mcp --help &>/dev/null 2>&1; then
    echo "[openocd-mcp] Installation complete."
else
    echo "[openocd-mcp] ERROR: openocd-mcp not found after install" >&2
    exit 1
fi

# Install udev rules for common JTAG/SWD probes
if [ -d /etc/udev/rules.d ]; then
    echo "[openocd-mcp] Installing udev rules..."
    sudo cp "$(dirname "$0")/assets/90-limpet-jtag-swd.rules" /etc/udev/rules.d/ 2>/dev/null || true
    sudo udevadm control --reload-rules 2>/dev/null || true
fi

echo "[openocd-mcp] Plugin ready."
