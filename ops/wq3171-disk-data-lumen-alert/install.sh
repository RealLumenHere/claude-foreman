#!/usr/bin/env bash
# wq#3171 lumen-disk-pct-alarm install. Run as root on Lumen, e.g.:
#   lumen-run start --root -- bash /home/lumen/foreman/ops/wq3171-disk-data-lumen-alert/install.sh
# Not run by this BUILD worker — the sandbox has no systemctl write access and
# /etc is outside the worktree (see ops/wq1299-phoenix-tracer/install.sh for
# the same constraint on this repo's other local timer installs).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vunit_file "$HERE/lumen-disk-pct-alarm.service" /etc/systemd/system/lumen-disk-pct-alarm.service
  vunit_file "$HERE/lumen-disk-pct-alarm.timer"   /etc/systemd/system/lumen-disk-pct-alarm.timer
  venabled lumen-disk-pct-alarm.timer
  vactive lumen-disk-pct-alarm.timer
  vdone
fi

sudo install -m 0644 "$HERE/lumen-disk-pct-alarm.service" /etc/systemd/system/lumen-disk-pct-alarm.service
sudo install -m 0644 "$HERE/lumen-disk-pct-alarm.timer" /etc/systemd/system/lumen-disk-pct-alarm.timer
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-disk-pct-alarm.timer

echo "--- verify ---"
systemctl list-timers lumen-disk-pct-alarm.timer --no-pager --no-legend
echo "dry run:    python3 $HERE/disk_pct_alarm.py check --dry-run --json"
echo "manual run: systemctl start lumen-disk-pct-alarm.service"
echo "logs:       journalctl -u lumen-disk-pct-alarm.service -n 100 --no-pager"
echo "rollback:   systemctl disable --now lumen-disk-pct-alarm.timer; rm /etc/systemd/system/lumen-disk-pct-alarm.service /etc/systemd/system/lumen-disk-pct-alarm.timer"
