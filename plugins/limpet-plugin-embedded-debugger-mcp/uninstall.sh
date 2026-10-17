#!/bin/bash
set -euo pipefail
echo "[embedded-debugger-mcp] Uninstalling..."
cargo uninstall embedded-debugger-mcp 2>/dev/null || true
sudo rm -f /etc/udev/rules.d/90-limpet-debug-probe.rules 2>/dev/null || true
sudo udevadm control --reload-rules 2>/dev/null || true
echo "[embedded-debugger-mcp] Uninstalled."
