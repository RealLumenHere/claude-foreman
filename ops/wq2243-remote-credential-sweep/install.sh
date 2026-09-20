#!/usr/bin/env bash
# wq#2243 remote-credential-sweep install. Run as root, e.g.:
#   lumen-run start --root -- bash /home/lumen/foreman/ops/wq2243-remote-credential-sweep/install.sh
# Idempotent. The script runs in place from the foreman checkout, so only the units are copied.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

cp "$SRC/remote-credential-sweep.service" "$SRC/remote-credential-sweep.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now remote-credential-sweep.timer

echo "remote-credential-sweep.timer installed; first run at boot+10min, then hourly"
echo "manual run:   systemctl start remote-credential-sweep.service"
echo "dry scan:     python3 $SRC/remote_credential_sweep.py scan"
echo "logs:         journalctl -u remote-credential-sweep.service -n 100 --no-pager"
