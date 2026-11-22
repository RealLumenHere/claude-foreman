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
  # wq#5271: intended-ON again. The 2026-10-08 subtraction disabled it
  # (scratch/subtraction/timers-disable-phase2.txt:3) with an explicit re-enable condition,
  # "if DC-on-Lumen is used again" (scratch/subtraction/credential-consolidation.md:27).
  # Commander has run on Lumen since wq#4921 (2026-11-06); on 2026-11-22 ~/.dc-handles.db
  # was being written (WAL 09:36Z) and `dc-handles-scrub --check` found row_hits=4
  # raw_hits=2. Re-enabled 11:07Z; first pass: rows_changed=2 raw_hits_after=0.
  vtimer_scheduled dc-handles-scrub.timer
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
