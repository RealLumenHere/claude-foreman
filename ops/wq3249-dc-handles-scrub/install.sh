#!/usr/bin/env bash
# wq#3249 - install the dc-handles-scrub timer. Run as root via `lumen-run --root` (repo convention).
# Idempotent. The tool itself lives in lumen-bin (/home/lumen/bin/dc-handles-scrub); this bundle is the units.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vunit_file "$SRC/dc-handles-scrub.service" /etc/systemd/system/dc-handles-scrub.service
  vunit_file "$SRC/dc-handles-scrub.timer"   /etc/systemd/system/dc-handles-scrub.timer
  venabled   dc-handles-scrub.timer
  vactive    dc-handles-scrub.timer
  # wq#4910: no vtimer_scheduled for dc-handles-scrub.timer: intended-off, disabled by the
  # 2026-10-08 subtraction (scratch/subtraction/timers-disable-phase2.txt). Re-enabling it
  # is a decision, not a heal; whoever makes it adds vtimer_scheduled here.
  vdone
fi

[ -x /home/lumen/bin/dc-handles-scrub ] || { echo "missing /home/lumen/bin/dc-handles-scrub (land lumen-bin first)"; exit 1; }
echo "== read-only check before enabling =="
sudo -u lumen /home/lumen/bin/dc-handles-scrub --check || echo "(hits found; the first pass will scrub them)"
echo "== systemd units =="
install -m 644 "$SRC/dc-handles-scrub.service" /etc/systemd/system/dc-handles-scrub.service
install -m 644 "$SRC/dc-handles-scrub.timer"   /etc/systemd/system/dc-handles-scrub.timer
systemctl daemon-reload
systemctl enable --now dc-handles-scrub.timer
systemctl list-timers dc-handles-scrub.timer --no-pager || true
echo "installed. Watch: journalctl -u dc-handles-scrub -n 20"
