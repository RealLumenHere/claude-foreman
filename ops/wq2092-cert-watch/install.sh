#!/usr/bin/env bash
# wq#2092 lumen-cert-watch install. Run as root, e.g.:
#   lumen-run start --root -- bash /home/lumen/foreman/ops/wq2092-cert-watch/install.sh
# (this repo's convention for privileged unit installs — see
# ops/wq1397-gitea-version-check).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/lumen/services/lumen-cert-watch

mkdir -p "$DEST"
install -o lumen -g lumen -m 755 "$SRC/cert_watch.py" "$DEST/cert_watch.py"
# targets.json is operator-edited (extra internal endpoints): never overwrite it.
[ -e "$DEST/targets.json" ] || install -o lumen -g lumen -m 644 "$SRC/targets.json" "$DEST/targets.json"

cp "$SRC/lumen-cert-watch.service" "$SRC/lumen-cert-watch.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lumen-cert-watch.timer

echo "lumen-cert-watch.timer installed; first run at boot+10min, then every 24h"
echo "dry run:    python3 $DEST/cert_watch.py run --dry-run"
echo "manual run: systemctl start lumen-cert-watch.service"
echo "logs:       journalctl -u lumen-cert-watch.service -n 100 --no-pager"
echo "rollback:   systemctl disable --now lumen-cert-watch.timer; rm /etc/systemd/system/lumen-cert-watch.service /etc/systemd/system/lumen-cert-watch.timer; rm -r $DEST"
