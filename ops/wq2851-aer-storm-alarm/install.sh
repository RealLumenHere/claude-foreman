#!/usr/bin/env bash
# wq#2851 lumen-aer-storm-alarm install. Run as root, e.g.:
#   lumen-run start --root -- bash /home/lumen/foreman/ops/wq2851-aer-storm-alarm/install.sh
# (this repo's convention for privileged unit installs — see ops/wq1397-gitea-version-check).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/lumen/services/lumen-aer-storm-alarm

mkdir -p "$DEST"
install -o lumen -g lumen -m 755 "$SRC/aer_storm_alarm.py" "$DEST/aer_storm_alarm.py"
install -o lumen -g lumen -m 644 "$SRC/aer_storm_alarm.yaml" "$DEST/aer_storm_alarm.yaml"

cp "$SRC/lumen-aer-storm-alarm.service" "$SRC/lumen-aer-storm-alarm.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lumen-aer-storm-alarm.timer

echo "lumen-aer-storm-alarm.timer installed; first run at boot+2min, then every 5min"
echo "dry run:    python3 $DEST/aer_storm_alarm.py check --dry-run --json"
echo "manual run: systemctl start lumen-aer-storm-alarm.service"
echo "baselines:  python3 $DEST/aer_storm_alarm.py baseline --hours 24"
echo "logs:       journalctl -u lumen-aer-storm-alarm.service -n 100 --no-pager"
echo "rollback:   systemctl disable --now lumen-aer-storm-alarm.timer; rm /etc/systemd/system/lumen-aer-storm-alarm.service /etc/systemd/system/lumen-aer-storm-alarm.timer; rm -r $DEST"
