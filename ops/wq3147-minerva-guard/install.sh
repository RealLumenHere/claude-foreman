#!/usr/bin/env bash
# wq#3147 lumen-minerva-guard install. Run as root, e.g.:
#   lumen-run start --root -- bash /home/lumen/foreman/ops/wq3147-minerva-guard/install.sh
# (this repo's convention for privileged unit installs — see ops/wq2092-cert-watch).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/lumen/services/lumen-minerva-guard

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/minerva_guard.py" "$DEST/minerva_guard.py"
  vunit_file "$SRC/lumen-minerva-guard.service" /etc/systemd/system/lumen-minerva-guard.service
  vunit_file "$SRC/lumen-minerva-guard.timer" /etc/systemd/system/lumen-minerva-guard.timer
  vactive lumen-minerva-guard.timer
  # wq#4910: no vtimer_scheduled for lumen-minerva-guard.timer: intended-off, disabled by the
  # 2026-10-08 subtraction (scratch/subtraction/timers-disable-list.txt). Re-enabling it
  # is a decision, not a heal; whoever makes it adds vtimer_scheduled here.
  venabled lumen-minerva-guard.timer
  vdone
fi

mkdir -p "$DEST"
chown lumen:lumen "$DEST"
install -o lumen -g lumen -m 755 "$SRC/minerva_guard.py" "$DEST/minerva_guard.py"

cp "$SRC/lumen-minerva-guard.service" "$SRC/lumen-minerva-guard.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lumen-minerva-guard.timer

echo "lumen-minerva-guard.timer installed; first run at boot+15min, then every 6h"
echo "dry run:    python3 $DEST/minerva_guard.py run --dry-run"
echo "pre-land:   python3 $DEST/minerva_guard.py check --rev <branch>"
echo "manual run: systemctl start lumen-minerva-guard.service"
echo "logs:       journalctl -u lumen-minerva-guard.service -n 100 --no-pager"
echo "rollback:   systemctl disable --now lumen-minerva-guard.timer; rm /etc/systemd/system/lumen-minerva-guard.service /etc/systemd/system/lumen-minerva-guard.timer; rm -r $DEST"
