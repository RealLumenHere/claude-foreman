#!/usr/bin/env bash
# wq#4599 - install the secret-backup-sweep timer. Run as root via `lumen-run` (repo convention). Idempotent.
# The tool lives in lumen-bin (/home/lumen/bin/secret-backup-sweep); this bundle is the units.
# The timer REPORTS and raises aq rows. It only SHREDS when ~/.maint-ops/secret-backup-sweep.auto exists.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vunit_file "$SRC/secret-backup-sweep.service" /etc/systemd/system/secret-backup-sweep.service
  vunit_file "$SRC/secret-backup-sweep.timer"   /etc/systemd/system/secret-backup-sweep.timer
  venabled   secret-backup-sweep.timer
  vactive    secret-backup-sweep.timer
  vtimer_scheduled secret-backup-sweep.timer  # wq#4910: vactive also passes SubState=elapsed
  vdone
fi

[ -x /home/lumen/bin/secret-backup-sweep ] || { echo "missing /home/lumen/bin/secret-backup-sweep (land lumen-bin first)"; exit 1; }
echo "== read-only check before enabling =="
sudo -u lumen /home/lumen/bin/secret-backup-sweep report | tail -3
echo "== systemd units =="
install -m 644 "$SRC/secret-backup-sweep.service" /etc/systemd/system/secret-backup-sweep.service
install -m 644 "$SRC/secret-backup-sweep.timer"   /etc/systemd/system/secret-backup-sweep.timer
systemctl daemon-reload
systemctl enable --now secret-backup-sweep.timer
systemctl list-timers secret-backup-sweep.timer --no-pager || true
[ -e /home/lumen/.maint-ops/secret-backup-sweep.auto ] && echo "auto-shred: ARMED" || echo "auto-shred: OFF (touch /home/lumen/.maint-ops/secret-backup-sweep.auto to arm)"
echo "installed. Watch: journalctl -u secret-backup-sweep -n 20; tail /home/lumen/logs/secret-backup-sweep.log"
