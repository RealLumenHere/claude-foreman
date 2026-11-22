#!/usr/bin/env bash
# wq#1294 session-handoff install. Run as root, e.g.:
#   lumen-run start --root -- bash /home/lumen/foreman/ops/wq1294-session-handoff/install.sh
# (this repo's convention for privileged unit installs — see ops/wq900-primary-tree-guard).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vunit_file "$SRC/session-handoff.service" /etc/systemd/system/session-handoff.service
  vunit_file "$SRC/session-handoff.timer"   /etc/systemd/system/session-handoff.timer
  venabled   session-handoff.timer
  vactive    session-handoff.timer
  # wq#4910: no vtimer_scheduled for session-handoff.timer: intended-off, disabled by the
  # 2026-10-08 subtraction (scratch/subtraction/timers-disable-phase2.txt). Re-enabling it
  # is a decision, not a heal; whoever makes it adds vtimer_scheduled here.
  vdone
fi

cp "$SRC/session-handoff.service" "$SRC/session-handoff.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now session-handoff.timer

echo "session-handoff.timer installed; first run at boot+3min, then every 30min"
echo "manual run:   systemctl start session-handoff.service"
echo "on-demand:    python3 /home/lumen/bin/session-handoff [--note \"...\"]"
echo "logs:         journalctl -u session-handoff.service -n 100 --no-pager"
