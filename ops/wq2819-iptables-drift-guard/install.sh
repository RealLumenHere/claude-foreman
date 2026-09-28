#!/usr/bin/env bash
# wq#2819 lumen-iptables-drift-guard install. Run as root, e.g.:
#   lumen-run start --root -- bash /home/lumen/foreman/ops/wq2819-iptables-drift-guard/install.sh
# (this repo's convention for privileged unit installs — see ops/wq2092-cert-watch).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/lumen/services/lumen-iptables-drift-guard

mkdir -p "$DEST"
install -o root -g root -m 755 "$SRC/iptables_drift_guard.py" "$DEST/iptables_drift_guard.py"

cp "$SRC/lumen-iptables-drift-guard.service" "$SRC/lumen-iptables-drift-guard.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lumen-iptables-drift-guard.timer

echo "lumen-iptables-drift-guard.timer installed; first run at boot+2min, then every 15min"
echo "dry run:    python3 $DEST/iptables_drift_guard.py run --dry-run"
echo "manual run: systemctl start lumen-iptables-drift-guard.service"
echo "logs:       journalctl -u lumen-iptables-drift-guard.service -n 100 --no-pager"
echo "rollback:   systemctl disable --now lumen-iptables-drift-guard.timer; rm /etc/systemd/system/lumen-iptables-drift-guard.{service,timer}; rm -r $DEST"
