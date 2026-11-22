#!/usr/bin/env bash
# wq#3147 lumen-minerva-guard install. Run as root, e.g.:
#   lumen-run start --root -- bash /home/lumen/foreman/ops/wq3147-minerva-guard/install.sh
# (this repo's convention for privileged unit installs — see ops/wq2092-cert-watch).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/lumen/services/lumen-minerva-guard
# wq#5325: 0 = intended-off (disabled by the 2026-10-08 subtraction,
# scratch/subtraction/timers-disable-list.txt:4). A deliberate re-enable flips this to 1,
# which makes install enable+start the timer and --verify assert it enabled, active and
# scheduled. Not an env override: the intent lives here.
INTENDED_ON=0

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/minerva_guard.py" "$DEST/minerva_guard.py"
  vunit_file "$SRC/lumen-minerva-guard.service" /etc/systemd/system/lumen-minerva-guard.service
  vunit_file "$SRC/lumen-minerva-guard.timer" /etc/systemd/system/lumen-minerva-guard.timer
  # wq#5269: lumen-minerva-guard.timer is intended-off, disabled by the 2026-10-08
  # subtraction (scratch/subtraction/timers-disable-list.txt:4; phase2-results.txt:4
  # confirms inactive/disabled). Asserting it enabled+active made --verify fail forever
  # and filed an ops-root-apply telling the reader to run this script as root. The script
  # and unit files are still checked above; the timer's state is only when INTENDED_ON=1
  # (wq#5325, same pattern as wq#5266/wq#5268).
  if [ "$INTENDED_ON" = 1 ]; then
    venabled         lumen-minerva-guard.timer
    vactive          lumen-minerva-guard.timer
    vtimer_scheduled lumen-minerva-guard.timer
  fi
  vdone
fi

mkdir -p "$DEST"
chown lumen:lumen "$DEST"
install -o lumen -g lumen -m 755 "$SRC/minerva_guard.py" "$DEST/minerva_guard.py"

cp "$SRC/lumen-minerva-guard.service" "$SRC/lumen-minerva-guard.timer" /etc/systemd/system/
systemctl daemon-reload
if [ "$INTENDED_ON" = 1 ]; then
  systemctl enable --now lumen-minerva-guard.timer
  echo "lumen-minerva-guard.timer installed; first run at boot+15min, then every 6h"
else
  # wq#5325: intended-off; installing files must not undo the 2026-10-08 subtraction.
  echo "lumen-minerva-guard units installed; timer left as-is (intended-off, INTENDED_ON=0)"
fi
echo "dry run:    python3 $DEST/minerva_guard.py run --dry-run"
echo "pre-land:   python3 $DEST/minerva_guard.py check --rev <branch>"
echo "manual run: systemctl start lumen-minerva-guard.service"
echo "logs:       journalctl -u lumen-minerva-guard.service -n 100 --no-pager"
echo "rollback:   systemctl disable --now lumen-minerva-guard.timer; rm /etc/systemd/system/lumen-minerva-guard.service /etc/systemd/system/lumen-minerva-guard.timer; rm -r $DEST"
