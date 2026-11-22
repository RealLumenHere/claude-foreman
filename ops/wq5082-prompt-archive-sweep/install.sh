#!/usr/bin/env bash
# wq#5082 prompt-archive-sweep timer install. Run as a user with sudo on Lumen:
#   bash /home/lumen/foreman/ops/wq5082-prompt-archive-sweep/install.sh
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=/home/lumen/bin/prompt-archive-sweep

# wq#1696 deploy-verify contract. Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vfile "$HERE/prompt-archive-sweep" "$BIN"
  vunit_file "$HERE/prompt-archive-sweep.service" /etc/systemd/system/prompt-archive-sweep.service
  vunit_file "$HERE/prompt-archive-sweep.timer"   /etc/systemd/system/prompt-archive-sweep.timer
  venabled prompt-archive-sweep.timer
  vactive prompt-archive-sweep.timer
  vtimer_scheduled prompt-archive-sweep.timer  # wq#4910: vactive also passes SubState=elapsed
  vflag "$BIN" --dry-run
  vdone
fi

# The binary lands through the lumen-bin PR (#291); this bundle owns the unit and timer only.
[ -x "$BIN" ] || { echo "missing $BIN (land lumen-bin PR #291 first)"; exit 1; }
"$BIN" --help 2>&1 | grep -q -- --dry-run || { echo "installed $BIN has no --dry-run; wrong build"; exit 1; }
cmp -s "$HERE/prompt-archive-sweep" "$BIN" || echo "NOTE: $BIN differs from the bundle copy (ok if bin moved on by PR)"
"$BIN" --dry-run >/dev/null || { echo "dry run failed; not installing"; exit 1; }

sudo install -m 0644 "$HERE/prompt-archive-sweep.service" /etc/systemd/system/prompt-archive-sweep.service
sudo install -m 0644 "$HERE/prompt-archive-sweep.timer"   /etc/systemd/system/prompt-archive-sweep.timer
sudo systemctl daemon-reload
sudo systemctl enable --now prompt-archive-sweep.timer

echo "--- verify ---"
systemctl list-timers prompt-archive-sweep.timer --no-pager --no-legend
echo "manual run: sudo systemctl start prompt-archive-sweep.service ; journalctl -u prompt-archive-sweep.service -n 20 --no-pager"
echo "dry run:    $BIN --dry-run"
echo "log:        tail /home/lumen/logs/prompt-archive-sweep.log"
echo "rollback:   sudo systemctl disable --now prompt-archive-sweep.timer; sudo rm /etc/systemd/system/prompt-archive-sweep.{service,timer}; sudo systemctl daemon-reload"
