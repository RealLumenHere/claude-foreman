#!/usr/bin/env bash
# wq#2819 lumen-iptables-drift-guard install. Run as root, e.g.:
#   lumen-run start --root -- bash /home/lumen/foreman/ops/wq2819-iptables-drift-guard/install.sh
# (this repo's convention for privileged unit installs — see ops/wq2092-cert-watch).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
# wq#5266: 0 = intended-off (disabled by the 2026-10-08 subtraction). A deliberate
# re-enable flips this to 1, which makes install enable+start the timer and --verify
# assert it enabled, active and scheduled. Not an env override: the intent lives here.
IPTABLES_DRIFT_GUARD_ON=0

# wq#1696 deploy-verify contract (ops/_verify.sh), added by wq#2860. Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile      "$SRC/iptables_drift_guard.py" /home/lumen/services/lumen-iptables-drift-guard/iptables_drift_guard.py
  vunit_file "$SRC/lumen-iptables-drift-guard.service" /etc/systemd/system/lumen-iptables-drift-guard.service
  vunit_file "$SRC/lumen-iptables-drift-guard.timer" /etc/systemd/system/lumen-iptables-drift-guard.timer
  # wq#5266: lumen-iptables-drift-guard.timer is intended-off: the 2026-10-08 subtraction
  # disabled it on purpose (/home/lumen/scratch/subtraction/timers-disable-phase2.txt:26,
  # timer-massacre-phase2.md:63). wq#4910 dropped vtimer_scheduled for that reason but left
  # venabled/vactive, so --verify failed forever and filed an ops-root-apply telling the
  # reader to run this script as root, whose `enable --now` would have undone the
  # subtraction (same class as wq#5259/wq#5277). The unit files and installed copy are
  # still verified; the timer's state is not asserted. Re-enabling it is a decision, not a
  # heal: whoever makes it sets IPTABLES_DRIFT_GUARD_ON=1 above.
  if [ "$IPTABLES_DRIFT_GUARD_ON" = 1 ]; then
    venabled         lumen-iptables-drift-guard.timer
    vactive          lumen-iptables-drift-guard.timer
    vtimer_scheduled lumen-iptables-drift-guard.timer
  fi
  vdone
fi
DEST=/home/lumen/services/lumen-iptables-drift-guard

mkdir -p "$DEST"
install -o root -g root -m 755 "$SRC/iptables_drift_guard.py" "$DEST/iptables_drift_guard.py"

cp "$SRC/lumen-iptables-drift-guard.service" "$SRC/lumen-iptables-drift-guard.timer" /etc/systemd/system/
systemctl daemon-reload
if [ "$IPTABLES_DRIFT_GUARD_ON" = 1 ]; then
  systemctl enable --now lumen-iptables-drift-guard.timer
  echo "lumen-iptables-drift-guard.timer installed; first run at boot+2min, then every 15min"
else
  # wq#5266: intended-off; installing files must not undo the 2026-10-08 subtraction.
  echo "lumen-iptables-drift-guard units installed; timer left as-is (intended-off, IPTABLES_DRIFT_GUARD_ON=0)"
fi
echo "dry run:    python3 $DEST/iptables_drift_guard.py run --dry-run"
echo "manual run: systemctl start lumen-iptables-drift-guard.service"
echo "logs:       journalctl -u lumen-iptables-drift-guard.service -n 100 --no-pager"
echo "rollback:   systemctl disable --now lumen-iptables-drift-guard.timer; rm /etc/systemd/system/lumen-iptables-drift-guard.{service,timer}; rm -r $DEST"
