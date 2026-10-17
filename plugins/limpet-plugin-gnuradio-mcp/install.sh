#!/bin/bash
# limpet-plugin-gnuradio-mcp install script
# wq#4144 — installs gnuradio-mcp-server from PyPI
set -euo pipefail

echo "[gnuradio-mcp] Installing gnuradio-mcp plugin..."

if ! command -v python3 &>/dev/null; then
    echo "[gnuradio-mcp] ERROR: Python 3.10+ is required and was not found" >&2
    exit 1
fi

echo "[gnuradio-mcp] Installing gnuradio-mcp-server from PyPI..."
sudo python3 -m pip install "gnuradio-mcp-server==0.1.1"

if command -v gnuradio-mcp &>/dev/null; then
    echo "[gnuradio-mcp] Installation complete."
else
    echo "[gnuradio-mcp] ERROR: gnuradio-mcp not found after install" >&2
    exit 1
fi

if [ -d /etc/udev/rules.d ]; then
    echo "[gnuradio-mcp] Installing udev rules..."
    sudo cp "$(dirname "$0")/assets/90-limpet-gnuradio-mcp.rules" /etc/udev/rules.d/ 2>/dev/null || true
    sudo udevadm control --reload-rules 2>/dev/null || true
fi

echo "[gnuradio-mcp] Plugin ready."
echo "[gnuradio-mcp] Note: GNU Radio itself is a system dependency and is NOT"
echo "[gnuradio-mcp] installed by this script: apt install gnuradio gr-osmosdr"
echo "[gnuradio-mcp] (HackRF/RTL-SDR) or uhd-host (USRP). A flowgraph with an"
echo "[gnuradio-mcp] XML-RPC server block (port 8080) and a complex64 ZMQ PUB"
echo "[gnuradio-mcp] sink (port 5555) must be running before any tool works."
echo "[gnuradio-mcp] RTL-SDR also needs dvb_usb_rtl28xxu blacklisted — see"
echo "[gnuradio-mcp] limpet/hardware.d/09-sdr.toml."
