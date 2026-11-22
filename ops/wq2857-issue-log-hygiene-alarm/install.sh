#!/usr/bin/env bash
# wq#2857 issue-log-hygiene install. Run as root, e.g.:
#   lumen-run start --root -- bash /home/lumen/foreman/ops/wq2857-issue-log-hygiene-alarm/install.sh
# (this repo's convention for privileged unit installs — see ops/wq900-primary-tree-guard).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

# wq#5268: 0 = intended-off (disabled by the 2026-10-08 subtraction). A deliberate
# re-enable flips this to 1, which makes install enable+start the timer and --verify
# assert it enabled, active and scheduled. Not an env override: the intent lives here.
HYGIENE_ON=0

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
#
# wq#2877 added this handler. This bundle is what makes wq#2817's probe_pointers.py live:
# the probe is invoked in place from the primary tree by the timer below, never copied to a
# deploy root, so the units ARE the liveness fact and there is nothing else to byte-match.
# Without a handler the wq#2857 ticket closed `done` at 04:23 with issue-log-hygiene.timer
# reading `not-found` on the box -- merged, never live, and unalertable because
# ops-deploy-drift only raises aq rows for bundles that answer --verify
# (/home/lumen/bin/ops-deploy-drift:136).
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vunit_file "$SRC/issue-log-hygiene.service" /etc/systemd/system/issue-log-hygiene.service
  vunit_file "$SRC/issue-log-hygiene.timer"   /etc/systemd/system/issue-log-hygiene.timer
  # wq#5268: issue-log-hygiene.timer is intended-off: the 2026-10-08 subtraction disabled it
  # on purpose (/home/lumen/scratch/subtraction/timers-disable-list.txt:60). wq#4910 dropped
  # vtimer_scheduled for that reason but left venabled/vactive, so --verify failed forever
  # and filed an ops-root-apply telling the reader to run this script as root, whose
  # `enable --now` would have undone the subtraction (same class as wq#5264/wq#5257).
  # The unit files are still verified; the timer's state is only when HYGIENE_ON=1.
  if [ "$HYGIENE_ON" = 1 ]; then
    venabled         issue-log-hygiene.timer
    vactive          issue-log-hygiene.timer
    vtimer_scheduled issue-log-hygiene.timer
  fi
  vdone
fi

cp "$SRC/issue-log-hygiene.service" "$SRC/issue-log-hygiene.timer" /etc/systemd/system/
systemctl daemon-reload
if [ "$HYGIENE_ON" = 1 ]; then
  systemctl enable --now issue-log-hygiene.timer
else
  # wq#5268: intended-off; installing files must not undo the 2026-10-08 subtraction.
  echo "issue-log-hygiene units installed; timer left as-is (intended-off, HYGIENE_ON=0)"
  exit 0
fi

echo "issue-log-hygiene.timer installed; first run at boot+3min, then every 30min"
echo "manual run:   systemctl start issue-log-hygiene.service"
echo "on-demand:    python3 /home/lumen/foreman/ops/wq2817-issue-log-pointer-integrity/alarm_new_findings.py"
echo "logs:         journalctl -u issue-log-hygiene.service -n 100 --no-pager"
echo "last state:   cat /home/lumen/logs/.issue-log-hygiene.laststate"
