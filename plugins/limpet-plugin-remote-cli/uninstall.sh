#!/bin/bash
set -euo pipefail
echo "[shared-terminal] Uninstalling plugin..."
sudo rm -rf /opt/limpet/plugins/remote-cli
echo "[shared-terminal] Uninstalled. (shared-terminal itself, installed from lumen-bin, is untouched.)"
