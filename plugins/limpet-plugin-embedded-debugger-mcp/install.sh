#!/bin/bash
# limpet-plugin-embedded-debugger-mcp install script
# wq#4139 — builds embedded-debugger-mcp (Rust, probe-rs+OpenOCD dual backend) from
# the pinned upstream tag via cargo install.
set -euo pipefail

PIN_TAG="v0.3.0"
REPO_URL="https://github.com/Adancurusul/embedded-debugger-mcp.git"

echo "[embedded-debugger-mcp] Installing embedded-debugger-mcp plugin..."

if ! command -v cargo &>/dev/null; then
    echo "[embedded-debugger-mcp] ERROR: cargo (Rust toolchain) not found; install Rust stable first" >&2
    exit 1
fi

echo "[embedded-debugger-mcp] Building embedded-debugger-mcp @ ${PIN_TAG} from source..."
cargo install --locked --git "${REPO_URL}" --tag "${PIN_TAG}" embedded-debugger-mcp

# Verify installation
if command -v embedded-debugger-mcp &>/dev/null; then
    echo "[embedded-debugger-mcp] Installation complete."
else
    echo "[embedded-debugger-mcp] ERROR: embedded-debugger-mcp not found on PATH after install (check \$CARGO_HOME/bin)" >&2
    exit 1
fi

# Install udev rules for common probe-rs/OpenOCD probes
if [ -d /etc/udev/rules.d ]; then
    echo "[embedded-debugger-mcp] Installing udev rules..."
    sudo cp "$(dirname "$0")/assets/90-limpet-debug-probe.rules" /etc/udev/rules.d/ 2>/dev/null || true
    sudo udevadm control --reload-rules 2>/dev/null || true
fi

echo "[embedded-debugger-mcp] Plugin ready."
