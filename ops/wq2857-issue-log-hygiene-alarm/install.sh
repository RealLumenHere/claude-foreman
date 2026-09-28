#!/usr/bin/env bash
# wq#2857 issue-log-hygiene install. Run as root, e.g.:
#   lumen-run start --root -- bash /home/lumen/foreman/ops/wq2857-issue-log-hygiene-alarm/install.sh
# (this repo's convention for privileged unit installs — see ops/wq900-primary-tree-guard).
set -euo pipefail

SRC="$(dirname "$0")"

cp "$SRC/issue-log-hygiene.service" "$SRC/issue-log-hygiene.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now issue-log-hygiene.timer

echo "issue-log-hygiene.timer installed; first run at boot+3min, then every 30min"
echo "manual run:   systemctl start issue-log-hygiene.service"
echo "on-demand:    python3 /home/lumen/foreman/ops/wq2817-issue-log-pointer-integrity/alarm_new_findings.py"
echo "logs:         journalctl -u issue-log-hygiene.service -n 100 --no-pager"
echo "last state:   cat /home/lumen/logs/.issue-log-hygiene.laststate"
