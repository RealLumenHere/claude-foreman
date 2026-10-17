#!/bin/bash
# limpet-plugin-buspirate-mcp install script
# wq#4155 — installs mplogas/buspirate-mcp from a pinned git clone (no PyPI release).
set -euo pipefail

PIN="36589d5d9fbcb3018f8568a88cdad04a7c434299"
INSTALL_DIR="/opt/limpet/plugins/buspirate-mcp"
SRC_DIR="$INSTALL_DIR/src"

echo "[buspirate-mcp] Installing buspirate-mcp plugin (pin $PIN)..."

if ! command -v python3 &>/dev/null; then
    echo "[buspirate-mcp] ERROR: Python 3.11+ is required and was not found" >&2
    exit 1
fi
if ! command -v git &>/dev/null; then
    echo "[buspirate-mcp] ERROR: git is required and was not found" >&2
    exit 1
fi

sudo mkdir -p "$INSTALL_DIR"
sudo chown "$(id -u):$(id -g)" "$INSTALL_DIR"

if [ ! -d "$SRC_DIR" ]; then
    echo "[buspirate-mcp] Cloning mplogas/buspirate-mcp with submodules (vendor/bpio2)..."
    git clone --recurse-submodules https://github.com/mplogas/buspirate-mcp.git "$SRC_DIR"
fi
git -C "$SRC_DIR" fetch origin "$PIN"
git -C "$SRC_DIR" checkout "$PIN"
git -C "$SRC_DIR" submodule update --init --recursive

echo "[buspirate-mcp] Creating venv..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/python3" -m pip install --upgrade pip
"$INSTALL_DIR/venv/bin/python3" -m pip install "$SRC_DIR"
# esptool is a hard dependency even if ESP flash tools (enter_download_mode/read_flash)
# are never called — pyproject.toml declares it unconditionally.
"$INSTALL_DIR/venv/bin/python3" -m pip install "esptool>=4.0"

if "$INSTALL_DIR/venv/bin/python3" -c 'import buspirate_mcp' &>/dev/null; then
    echo "[buspirate-mcp] Installation complete."
else
    echo "[buspirate-mcp] ERROR: buspirate_mcp not importable after install" >&2
    exit 1
fi

if [ -d /etc/udev/rules.d ]; then
    echo "[buspirate-mcp] Installing udev rules..."
    sudo cp "$(dirname "$0")/assets/90-limpet-buspirate-mcp.rules" /etc/udev/rules.d/ 2>/dev/null || true
    sudo udevadm control --reload-rules 2>/dev/null || true
fi

echo "[buspirate-mcp] Plugin ready."
echo "[buspirate-mcp] Note: this wraps a Bus Pirate 6 only (BPIO2 interface, not v3/v5)."
echo "[buspirate-mcp] Do not trust upstream's own read-only/allowed-write/approval-write"
echo "[buspirate-mcp] tiers — see limpet-plugin.toml [mcp.tools_energize] for the real gate."
