#!/usr/bin/env bash
# wq#3215 lumen-stale-env-audit install. Run as a user with sudo on Lumen:
#   bash /home/lumen/foreman/ops/wq3215-stale-env-audit-timer/install.sh
# Or from a sandboxed worker: lumen-run start --root -- bash <this file>
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# wq#1696 deploy-verify contract. Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vunit_file "$HERE/lumen-stale-env-audit.service" /etc/systemd/system/lumen-stale-env-audit.service
  vunit_file "$HERE/lumen-stale-env-audit.timer"   /etc/systemd/system/lumen-stale-env-audit.timer
  venabled lumen-stale-env-audit.timer
  vactive lumen-stale-env-audit.timer
  vdone
fi

[ -x /home/lumen/bin/stale-env-audit ] || { echo "missing /home/lumen/bin/stale-env-audit (lumen-bin PR #209/#210)"; exit 1; }
sudo install -m 0644 "$HERE/lumen-stale-env-audit.service" /etc/systemd/system/lumen-stale-env-audit.service
sudo install -m 0644 "$HERE/lumen-stale-env-audit.timer" /etc/systemd/system/lumen-stale-env-audit.timer
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-stale-env-audit.timer

echo "--- verify ---"
systemctl list-timers lumen-stale-env-audit.timer --no-pager --no-legend
echo "manual run: sudo systemctl start lumen-stale-env-audit.service ; journalctl -u lumen-stale-env-audit.service -n 30 --no-pager"
echo "rollback:   sudo systemctl disable --now lumen-stale-env-audit.timer; sudo rm /etc/systemd/system/lumen-stale-env-audit.{service,timer}; sudo systemctl daemon-reload"
