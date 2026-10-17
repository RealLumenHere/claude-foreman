#!/bin/bash
# limpet-plugin-hackrf-mcp install script
# wq#4154 — installs thehappydinoa/hackrf-mcp from a pinned git clone (no PyPI release,
# no tags) and deploys this plugin's mcp/hackrf_tx_guard.py TX-refusal proxy in front of it.
set -euo pipefail

PIN="d9fa2ee6bf58acd28e50a1cad98a4458e0e2ef67"
INSTALL_DIR="/opt/limpet/plugins/hackrf-mcp"

echo "[hackrf-mcp] Installing hackrf-mcp plugin (pin $PIN)..."

if ! command -v python3 &>/dev/null; then
    echo "[hackrf-mcp] ERROR: Python 3.10+ is required and was not found" >&2
    exit 1
fi

if ! command -v hackrf_info &>/dev/null; then
    echo "[hackrf-mcp] Installing hackrf (system package providing hackrf_info/hackrf_transfer/hackrf_sweep)..."
    sudo apt-get update -qq && sudo apt-get install -y -qq hackrf
fi

sudo mkdir -p "$INSTALL_DIR/mcp"
sudo chown "$(id -u):$(id -g)" "$INSTALL_DIR" "$INSTALL_DIR/mcp"

echo "[hackrf-mcp] Creating venv..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/python3" -m pip install --upgrade pip
"$INSTALL_DIR/venv/bin/python3" -m pip install "git+https://github.com/thehappydinoa/hackrf-mcp@$PIN"

cp "$(dirname "$0")/mcp/hackrf_tx_guard.py" "$INSTALL_DIR/mcp/"
chmod +x "$INSTALL_DIR/mcp/hackrf_tx_guard.py"

if "$INSTALL_DIR/venv/bin/python3" -c 'import hackrf_mcp' &>/dev/null; then
    echo "[hackrf-mcp] Installation complete."
else
    echo "[hackrf-mcp] ERROR: hackrf_mcp not importable after install" >&2
    exit 1
fi

if [ -d /etc/udev/rules.d ]; then
    echo "[hackrf-mcp] Installing udev rules..."
    sudo cp "$(dirname "$0")/assets/90-limpet-hackrf-mcp.rules" /etc/udev/rules.d/ 2>/dev/null || true
    sudo udevadm control --reload-rules 2>/dev/null || true
fi

echo "[hackrf-mcp] Plugin ready."
echo "[hackrf-mcp] RX-only v1: hackrf_transfer direction=\"tx\" is refused by"
echo "[hackrf-mcp] mcp/hackrf_tx_guard.py regardless of energize-confirm, pending wq#4147."
echo "[hackrf-mcp] Do not run this alongside gnuradio-mcp's GNURADIO_HARDWARE=hackrf"
echo "[hackrf-mcp] backend — both open the same physical HackRF One device."
