#!/usr/bin/env bash
# wq#1397 gitea-version-check install. Run as root, e.g.:
#   lumen-run start --root -- bash /home/lumen/foreman/ops/wq1397-gitea-version-check/install.sh
# (this repo's convention for privileged unit installs — see
# ops/wq1298-gitleaks-detect, ops/wq859-renovate-gitea).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/lumen/services/gitea-version-check

mkdir -p "$DEST"
install -o lumen -g lumen -m 755 "$SRC/gitea_version_check.py" "$DEST/gitea_version_check.py"

cp "$SRC/gitea-version-check.service" "$SRC/gitea-version-check.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now gitea-version-check.timer

echo "gitea-version-check.timer installed; first run at boot+10min, then every 24h"
echo "manual run: systemctl start gitea-version-check.service"
echo "logs: journalctl -u gitea-version-check.service -n 100 --no-pager"
