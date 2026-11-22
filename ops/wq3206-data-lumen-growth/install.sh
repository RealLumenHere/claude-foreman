#!/usr/bin/env bash
# wq#3206 lumen-data-lumen-growth install. Run as root on Lumen, e.g.:
#   lumen-run start --root -- bash /home/lumen/foreman/ops/wq3206-data-lumen-growth/install.sh
# Not run by this BUILD worker directly at write time — same constraint noted
# in ops/wq3171-disk-data-lumen-alert/install.sh: the sandbox has no
# systemctl write access and /etc is outside the worktree. `lumen-run start
# --root` (a detached, non-interactive privileged runner) is the sanctioned
# escalation path and was used to actually install this on 2026-09-30.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vunit_file "$HERE/lumen-data-lumen-growth.service" /etc/systemd/system/lumen-data-lumen-growth.service
  vunit_file "$HERE/lumen-data-lumen-growth.timer"   /etc/systemd/system/lumen-data-lumen-growth.timer
  venabled lumen-data-lumen-growth.timer
  vactive lumen-data-lumen-growth.timer
  # wq#4910: no vtimer_scheduled for lumen-data-lumen-growth.timer: intended-off, disabled by the
  # 2026-10-08 subtraction (scratch/subtraction/timers-disable-list.txt). Re-enabling it
  # is a decision, not a heal; whoever makes it adds vtimer_scheduled here.
  vdone
fi

sudo install -m 0644 "$HERE/lumen-data-lumen-growth.service" /etc/systemd/system/lumen-data-lumen-growth.service
sudo install -m 0644 "$HERE/lumen-data-lumen-growth.timer" /etc/systemd/system/lumen-data-lumen-growth.timer
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-data-lumen-growth.timer

echo "--- verify ---"
systemctl list-timers lumen-data-lumen-growth.timer --no-pager --no-legend
echo "manual run: systemctl start lumen-data-lumen-growth.service"
echo "slope:      python3 $HERE/data_lumen_growth.py slope"
echo "logs:       journalctl -u lumen-data-lumen-growth.service -n 100 --no-pager"
echo "rollback:   systemctl disable --now lumen-data-lumen-growth.timer; rm /etc/systemd/system/lumen-data-lumen-growth.service /etc/systemd/system/lumen-data-lumen-growth.timer"
