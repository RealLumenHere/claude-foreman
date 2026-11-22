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

# wq#5270: 0 = intended-off (disabled by the 2026-10-08 subtraction as a host watcher
# Beszel duplicates: /home/lumen/scratch/subtraction/timers-disable-list.txt:44,
# REPORT.md:64). A deliberate re-enable flips this to 1, which makes install enable+start
# the timer and --verify assert it enabled, active and scheduled. Not an env override:
# the intent lives here.
GROWTH_ON=0

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vunit_file "$HERE/lumen-data-lumen-growth.service" /etc/systemd/system/lumen-data-lumen-growth.service
  vunit_file "$HERE/lumen-data-lumen-growth.timer"   /etc/systemd/system/lumen-data-lumen-growth.timer
  # wq#5270: wq#4910 dropped vtimer_scheduled because the timer is intended-off but kept
  # venabled/vactive, so --verify failed forever and filed an ops-root-apply telling the
  # reader to run this script as root, whose `enable --now` would have undone the
  # subtraction (same class as wq#5268/wq#5264). Unit files are still byte-verified; the
  # timer's state is only asserted when GROWTH_ON=1.
  if [ "$GROWTH_ON" = 1 ]; then
    venabled         lumen-data-lumen-growth.timer
    vactive          lumen-data-lumen-growth.timer
    vtimer_scheduled lumen-data-lumen-growth.timer
  fi
  vdone
fi

sudo install -m 0644 "$HERE/lumen-data-lumen-growth.service" /etc/systemd/system/lumen-data-lumen-growth.service
sudo install -m 0644 "$HERE/lumen-data-lumen-growth.timer" /etc/systemd/system/lumen-data-lumen-growth.timer
sudo systemctl daemon-reload
if [ "$GROWTH_ON" != 1 ]; then
  # wq#5270: intended-off; installing files must not undo the 2026-10-08 subtraction.
  echo "lumen-data-lumen-growth units installed; timer left as-is (intended-off, GROWTH_ON=0)"
  exit 0
fi
sudo systemctl enable --now lumen-data-lumen-growth.timer

echo "--- verify ---"
systemctl list-timers lumen-data-lumen-growth.timer --no-pager --no-legend
echo "manual run: systemctl start lumen-data-lumen-growth.service"
echo "slope:      python3 $HERE/data_lumen_growth.py slope"
echo "logs:       journalctl -u lumen-data-lumen-growth.service -n 100 --no-pager"
echo "rollback:   systemctl disable --now lumen-data-lumen-growth.timer; rm /etc/systemd/system/lumen-data-lumen-growth.service /etc/systemd/system/lumen-data-lumen-growth.timer"
