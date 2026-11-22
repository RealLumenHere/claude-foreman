#!/usr/bin/env bash
# wq#2819 lumen-iptables-drift-guard install. Run as root, e.g.:
#   lumen-run start --root -- bash /home/lumen/foreman/ops/wq2819-iptables-drift-guard/install.sh
# (this repo's convention for privileged unit installs — see ops/wq2092-cert-watch).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

# wq#1696 deploy-verify contract (ops/_verify.sh), added by wq#2860. Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile      "$SRC/iptables_drift_guard.py" /home/lumen/services/lumen-iptables-drift-guard/iptables_drift_guard.py
  vunit_file "$SRC/lumen-iptables-drift-guard.service" /etc/systemd/system/lumen-iptables-drift-guard.service
  vunit_file "$SRC/lumen-iptables-drift-guard.timer" /etc/systemd/system/lumen-iptables-drift-guard.timer
  venabled   lumen-iptables-drift-guard.timer
  vactive    lumen-iptables-drift-guard.timer
  # wq#4910: no vtimer_scheduled for lumen-iptables-drift-guard.timer: intended-off, disabled by the
  # 2026-10-08 subtraction (scratch/subtraction/timers-disable-phase2.txt). Re-enabling it
  # is a decision, not a heal; whoever makes it adds vtimer_scheduled here.
  vdone
fi
DEST=/home/lumen/services/lumen-iptables-drift-guard

mkdir -p "$DEST"
install -o root -g root -m 755 "$SRC/iptables_drift_guard.py" "$DEST/iptables_drift_guard.py"

cp "$SRC/lumen-iptables-drift-guard.service" "$SRC/lumen-iptables-drift-guard.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lumen-iptables-drift-guard.timer

echo "lumen-iptables-drift-guard.timer installed; first run at boot+2min, then every 15min"
echo "dry run:    python3 $DEST/iptables_drift_guard.py run --dry-run"
echo "manual run: systemctl start lumen-iptables-drift-guard.service"
echo "logs:       journalctl -u lumen-iptables-drift-guard.service -n 100 --no-pager"
echo "rollback:   systemctl disable --now lumen-iptables-drift-guard.timer; rm /etc/systemd/system/lumen-iptables-drift-guard.{service,timer}; rm -r $DEST"
