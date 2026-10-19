#!/bin/bash
# limpet-plugin-serial-mcp install script
# wq#4317 — installs alxgmpr/serial-mcp from PyPI (pyserial-mcp), pinned version.
set -euo pipefail

VERSION="0.7.4"
INSTALL_DIR="/opt/limpet/plugins/serial-mcp"

echo "[serial-mcp] Installing serial-mcp plugin (pyserial-mcp==$VERSION)..."

if ! command -v python3 &>/dev/null; then
    echo "[serial-mcp] ERROR: Python 3.10+ is required and was not found" >&2
    exit 1
fi

sudo mkdir -p "$INSTALL_DIR"
sudo chown "$(id -u):$(id -g)" "$INSTALL_DIR"

echo "[serial-mcp] Creating venv..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/python3" -m pip install --upgrade pip
"$INSTALL_DIR/venv/bin/python3" -m pip install "pyserial-mcp==$VERSION"

if "$INSTALL_DIR/venv/bin/python3" -c 'import serial_mcp' &>/dev/null; then
    echo "[serial-mcp] Installation complete."
else
    echo "[serial-mcp] ERROR: serial_mcp not importable after install" >&2
    exit 1
fi

if [ -d /etc/udev/rules.d ]; then
    echo "[serial-mcp] Installing udev rules..."
    sudo cp "$(dirname "$0")/assets/90-limpet-serial-mcp.rules" /etc/udev/rules.d/ 2>/dev/null || true
    sudo udevadm control --reload-rules 2>/dev/null || true
fi

echo "[serial-mcp] Plugin ready."
echo "[serial-mcp] Works with any in-kernel USB-serial adapter already cataloged in"
echo "[serial-mcp] limpet/hardware.d/05-usb-serial.toml (FTDI, CP210x, CH340, PL2303)."
