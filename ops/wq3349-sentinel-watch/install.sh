#!/usr/bin/env bash
# wq#3349 sentinel-watch install. Run as a user with sudo on Lumen:
#   bash /home/lumen/foreman/ops/wq3349-sentinel-watch/install.sh
# Unit names are lumen-sentinel-audit.*: lumen-sentinel-watch.* is the unrelated 5-minute Gatus watchdog.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=/home/lumen/bin/sentinel-watch
UNITS=(lumen-sentinel-audit.service lumen-sentinel-audit.timer)
# wq#5274: 0 = intended-off (disabled by the 2026-10-08 subtraction). A deliberate
# re-enable flips this to 1, which makes install enable+start the timer and --verify
# assert it enabled, active and scheduled. Not an env override: the intent lives here.
SENTINEL_AUDIT_ON=0

# wq#1696 deploy-verify contract. Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vfile "$HERE/sentinel-watch" "$BIN"
  for u in "${UNITS[@]}"; do vunit_file "$HERE/$u" "/etc/systemd/system/$u"; done
  # wq#5274: lumen-sentinel-audit.timer is intended-off: the 2026-10-08 subtraction disabled it
  # on purpose as a watcher-of-watchers (/home/lumen/scratch/subtraction/timers-disable-list.txt:73,
  # REPORT.md:67). wq#4910 dropped vtimer_scheduled for that reason but left venabled/vactive,
  # so --verify failed forever and filed an ops-root-apply telling the reader to run this
  # script as root, whose `enable --now` would have undone the subtraction (same class as
  # wq#5266). Unit files, the installed binary and its --check flag are still verified; the
  # timer's state is not asserted. Re-enabling is a decision: set SENTINEL_AUDIT_ON=1 above.
  if [ "$SENTINEL_AUDIT_ON" = 1 ]; then
    venabled         lumen-sentinel-audit.timer
    vactive          lumen-sentinel-audit.timer
    vtimer_scheduled lumen-sentinel-audit.timer
  fi
  vflag "$BIN" --check
  vdone
fi

python3 -m py_compile "$HERE/sentinel-watch"
sudo install -m 0755 -o lumen -g lumen "$HERE/sentinel-watch" "$BIN"
sudo install -d -o lumen -g lumen /home/lumen/agents/sentinel-watch /home/lumen/logs
for u in "${UNITS[@]}"; do sudo install -m 0644 "$HERE/$u" "/etc/systemd/system/$u"; done
sudo systemctl daemon-reload
if [ "$SENTINEL_AUDIT_ON" = 1 ]; then
  sudo systemctl enable --now lumen-sentinel-audit.timer
  echo "--- verify ---"
  systemctl list-timers lumen-sentinel-audit.timer --no-pager --no-legend
else
  # wq#5274: intended-off; installing files must not undo the 2026-10-08 subtraction.
  echo "lumen-sentinel-audit units installed; timer left as-is (intended-off, SENTINEL_AUDIT_ON=0)"
fi
echo "dry run:    sentinel-watch --dry-run"
echo "log:        tail -f /home/lumen/logs/sentinel-watch.log"
echo "rollback:   sudo systemctl disable --now lumen-sentinel-audit.timer; sudo rm /etc/systemd/system/lumen-sentinel-audit.{service,timer} $BIN; sudo systemctl daemon-reload"
