#!/usr/bin/env bash
# Install storage-manifest-drift (wq#1615): script -> /home/lumen/bin, units -> systemd.
# Idempotent: re-running converges to the same state. Needs root for the units; a BUILD
# worker runs it via `lumen-run start --root -- bash ops/wq1615-storage-manifest-drift/install.sh`.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
export PYTHONDONTWRITEBYTECODE=1  # runs as root; leave no root-owned __pycache__ behind

python3 -m pytest -q -p no:cacheprovider "$HERE/test_storage_manifest_drift.py"

install -o lumen -g lumen -m 0755 "$HERE/storage_manifest_drift.py" /home/lumen/bin/storage-manifest-drift
install -m 0644 "$HERE/storage-manifest-drift.service" /etc/systemd/system/storage-manifest-drift.service
install -m 0644 "$HERE/storage-manifest-drift.timer" /etc/systemd/system/storage-manifest-drift.timer
systemctl daemon-reload
systemctl enable --now storage-manifest-drift.timer
systemctl is-active --quiet storage-manifest-drift.timer
systemctl list-timers storage-manifest-drift.timer --no-pager --no-legend
