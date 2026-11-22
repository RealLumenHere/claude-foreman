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
  # wq#5251: no venabled/vactive/vtimer_scheduled for session-handoff.timer. It is
  # intended-off: the 2026-10-08 subtraction disabled it on purpose
  # (/home/lumen/scratch/subtraction/timers-disable-phase2.txt:54), and wq#1630 moved the
  # handoff render into `claude_context.sh --core` at boot. Asserting enabled+active made
  # --verify fail permanently, and the ops-root-apply it filed told the reader to run this
  # script as root, which runs `systemctl enable --now` and undoes the subtraction. The
  # unit files are still checked above. Re-enabling the timer is a decision, not a heal;
  # whoever makes it adds venabled/vactive/vtimer_scheduled back here.
  vdone
fi

cp "$SRC/session-handoff.service" "$SRC/session-handoff.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now session-handoff.timer

echo "session-handoff.timer installed; first run at boot+3min, then every 30min"
echo "manual run:   systemctl start session-handoff.service"
echo "on-demand:    python3 /home/lumen/bin/session-handoff [--note \"...\"]"
echo "logs:         journalctl -u session-handoff.service -n 100 --no-pager"
