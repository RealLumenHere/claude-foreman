#!/bin/bash
# limpet-plugin-mcjtag install script
# wq#4138 — installs mcjtag from PyPI + ensures OpenOCD is present
#
# SAME-PORT WARNING: mcjtag and openocd-mcp both default to OpenOCD TCL port
# 6666. Installing this plugin does not enable it ([mcp].enabled = false in
# limpet-plugin.toml) -- do not flip that to true while openocd-mcp is also
# enabled, they will race for the port.
set -euo pipefail

echo "[mcjtag] Installing mcjtag plugin (secondary/alternate to openocd-mcp)..."

# Ensure OpenOCD is installed (system package)
if ! command -v openocd &>/dev/null; then
    echo "[mcjtag] Installing OpenOCD..."
    sudo apt-get update -qq && sudo apt-get install -y -qq openocd
fi

# Install mcjtag from PyPI, pinned to the evaluated version
echo "[mcjtag] Installing mcjtag from PyPI..."
pip install --break-system-packages mcjtag==2026.2.18

# Verify installation
if command -v mcjtag &>/dev/null || python3 -m mcjtag --help &>/dev/null 2>&1; then
    echo "[mcjtag] Installation complete."
else
    echo "[mcjtag] ERROR: mcjtag not found after install" >&2
    exit 1
fi

# Install the shipped OpenOCD configs (upstream's sdist excludes *.cfg)
PLUGIN_DIR="$(dirname "$0")"
INSTALL_DIR="/opt/limpet/plugins/mcjtag"
sudo mkdir -p "$INSTALL_DIR/configs"
sudo cp "$PLUGIN_DIR/assets/openocd-daplink-swd.cfg" "$INSTALL_DIR/configs/" 2>/dev/null || true
sudo cp "$PLUGIN_DIR/assets/openocd-daplink-jtag.cfg" "$INSTALL_DIR/configs/" 2>/dev/null || true

# Install udev rules for the tested CMSIS-DAP probe (distinct filename from
# openocd-mcp's rules, so installing both plugins never clobbers a file)
if [ -d /etc/udev/rules.d ]; then
    echo "[mcjtag] Installing udev rules..."
    sudo cp "$PLUGIN_DIR/assets/91-limpet-jtag-swd-mcjtag.rules" /etc/udev/rules.d/ 2>/dev/null || true
    sudo udevadm control --reload-rules 2>/dev/null || true
fi

echo "[mcjtag] Plugin ready (disabled by default -- see README for the openocd-mcp port conflict before enabling)."
