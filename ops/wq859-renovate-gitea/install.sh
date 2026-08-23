#!/usr/bin/env bash
# wq#859 Renovate-on-Gitea install. Run as root via lumen-run --root on the
# target host itself (this repo's convention — see ops/wq394-netdata-canary).
set -euo pipefail

SRC="$(dirname "$0")"
DEST=/home/lumen/services/renovate-gitea

mkdir -p "$DEST/cache"
cp "$SRC/config.js" "$DEST/config.js"
chown -R lumen:lumen "$DEST"

cp "$SRC/renovate-gitea.service" "$SRC/renovate-gitea.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now renovate-gitea.timer

echo "renovate-gitea.timer installed; first run at boot+10min, then every 4h"
echo "manual run: systemctl start renovate-gitea.service"
echo "logs: journalctl -u renovate-gitea.service -n 100 --no-pager"
