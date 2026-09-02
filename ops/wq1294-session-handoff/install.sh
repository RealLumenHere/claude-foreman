#!/usr/bin/env bash
# wq#1294 session-handoff install. Run as root, e.g.:
#   lumen-run start --root -- bash /home/lumen/foreman/ops/wq1294-session-handoff/install.sh
# (this repo's convention for privileged unit installs — see ops/wq900-primary-tree-guard).
set -euo pipefail

SRC="$(dirname "$0")"

cp "$SRC/session-handoff.service" "$SRC/session-handoff.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now session-handoff.timer

echo "session-handoff.timer installed; first run at boot+3min, then every 30min"
echo "manual run:   systemctl start session-handoff.service"
echo "on-demand:    python3 /home/lumen/bin/session-handoff [--note \"...\"]"
echo "logs:         journalctl -u session-handoff.service -n 100 --no-pager"
