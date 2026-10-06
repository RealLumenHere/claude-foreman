#!/usr/bin/env bash
# wq#3349 sentinel-watch install. Run as a user with sudo on Lumen:
#   bash /home/lumen/foreman/ops/wq3349-sentinel-watch/install.sh
# Unit names are lumen-sentinel-audit.*: lumen-sentinel-watch.* is the unrelated 5-minute Gatus watchdog.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=/home/lumen/bin/sentinel-watch
UNITS=(lumen-sentinel-audit.service lumen-sentinel-audit.timer)

# wq#1696 deploy-verify contract. Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vfile "$HERE/sentinel-watch" "$BIN"
  for u in "${UNITS[@]}"; do vunit_file "$HERE/$u" "/etc/systemd/system/$u"; done
  venabled lumen-sentinel-audit.timer
  vactive lumen-sentinel-audit.timer
  vflag "$BIN" --check
  vdone
fi

python3 -m py_compile "$HERE/sentinel-watch"
sudo install -m 0755 -o lumen -g lumen "$HERE/sentinel-watch" "$BIN"
sudo install -d -o lumen -g lumen /home/lumen/agents/sentinel-watch /home/lumen/logs
for u in "${UNITS[@]}"; do sudo install -m 0644 "$HERE/$u" "/etc/systemd/system/$u"; done
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-sentinel-audit.timer
echo "--- verify ---"
systemctl list-timers lumen-sentinel-audit.timer --no-pager --no-legend
echo "dry run:    sentinel-watch --dry-run"
echo "log:        tail -f /home/lumen/logs/sentinel-watch.log"
echo "rollback:   sudo systemctl disable --now lumen-sentinel-audit.timer; sudo rm /etc/systemd/system/lumen-sentinel-audit.{service,timer} $BIN; sudo systemctl daemon-reload"
