#!/bin/bash
set -euo pipefail
echo "[remote-cli] Uninstalling plugin..."
sudo rm -rf /opt/limpet/plugins/remote-cli
echo "[remote-cli] Uninstalled. (remote-cli itself, installed by wq#4395, is untouched.)"
