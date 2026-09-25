#!/usr/bin/env bash
# Deploy wq#2570's lumen-monitor.service fix. Root step; run detached per
# instinct #5, same convention as token-engine/install.sh:
#
#   /home/lumen/bin/lumen-run start --root -- bash \
#       <repo>/ops/wq2570-lumen-monitor-restart-storm/install.sh
#
# Idempotent: re-running converges to the same state.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

install -o lumen -g lumen -m 0644 "$HERE/lumen_status_monitor.py" /home/lumen/agent/lumen_status_monitor.py
install -m 0644 "$HERE/lumen-monitor.service" /etc/systemd/system/lumen-monitor.service
systemctl daemon-reload
systemctl restart lumen-monitor.service
systemctl show -p NRestarts,ActiveEnterTimestamp,SubState lumen-monitor.service
