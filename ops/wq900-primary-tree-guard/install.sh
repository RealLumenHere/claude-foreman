#!/usr/bin/env bash
# wq#900 primary-tree-guard install. Run as root via lumen-run --root on lumen
# itself (this repo's convention — see ops/wq859-renovate-gitea).
#
# The service executes harness/primary_tree_guard.py from the /home/lumen/foreman
# PRIMARY tree — the same tree it guards. That is deliberate and safe: the script
# is a leaf (no repo-local imports needed at check time beyond the optional
# router.LANE_REPO union), and if a worker switches foreman's branch, whatever
# version of the script is on that branch still checks HEAD against main and
# restores it.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vunit_file "$SRC/primary-tree-guard.service" /etc/systemd/system/primary-tree-guard.service
  vunit_file "$SRC/primary-tree-guard.timer" /etc/systemd/system/primary-tree-guard.timer
  # wq#5276: no venabled/vactive/vtimer_scheduled for primary-tree-guard.timer:
  # intended-off, disabled by the 2026-10-08 subtraction
  # (scratch/subtraction/timers-disable-list.txt:66; phase2-results.txt:66 confirms
  # inactive/disabled). Asserting it enabled+active made --verify fail forever and filed
  # an ops-root-apply telling the reader to run this script as root, which runs
  # `systemctl enable --now` and undoes the subtraction. The unit files are still checked
  # above. Re-enabling is a decision, not a heal; whoever makes it adds
  # venabled/vactive/vtimer_scheduled back here.
  vdone
fi

cp "$SRC/primary-tree-guard.service" "$SRC/primary-tree-guard.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now primary-tree-guard.timer

echo "primary-tree-guard.timer installed; first run at boot+3min, then every 5min"
echo "manual run:   systemctl start primary-tree-guard.service"
echo "manual check: python3 /home/lumen/foreman/harness/primary_tree_guard.py status"
echo "logs:         journalctl -u primary-tree-guard.service -n 100 --no-pager"
