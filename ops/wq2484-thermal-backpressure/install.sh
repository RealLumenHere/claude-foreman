#!/usr/bin/env bash
# Install wq#2484's thermal-backpressure timer. Idempotent. Run from a root/interactive
# session on Lumen: the BUILD worker that wrote this has no sudo and cannot install a unit.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
GUARD=/home/lumen/foreman/ops/thermal_backpressure.py

# read-only first: every node should answer with a temperature and held=False
python3 "$GUARD" poll --dry-run
echo

sudo install -m 0644 "$HERE/lumen-thermal-backpressure.service" /etc/systemd/system/lumen-thermal-backpressure.service
sudo install -m 0644 "$HERE/lumen-thermal-backpressure.timer" /etc/systemd/system/lumen-thermal-backpressure.timer
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-thermal-backpressure.timer
systemctl list-timers lumen-thermal-backpressure.timer --no-pager --no-legend
echo
python3 "$GUARD" status
