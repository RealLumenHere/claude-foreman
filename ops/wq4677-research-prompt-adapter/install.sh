#!/usr/bin/env bash
# wq#4677 research-prompt-adapter timer install. Run as a user with sudo on Lumen:
#   bash /home/lumen/foreman/ops/wq4677-research-prompt-adapter/install.sh
# Or from a sandboxed worker: lumen-run start --root -- bash <this file>
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=/home/lumen/bin/research-prompt-adapter

# wq#1696 deploy-verify contract. Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  # The binary lands through the lumen-bin PR; this copy is byte-identical at ship time and
  # _vsuperseded accepts later reviewed commits to /home/lumen/bin (wq#3468).
  vfile "$HERE/research-prompt-adapter" "$BIN"
  vunit_file "$HERE/research-prompt-adapter.service" /etc/systemd/system/research-prompt-adapter.service
  vunit_file "$HERE/research-prompt-adapter.timer"   /etc/systemd/system/research-prompt-adapter.timer
  venabled research-prompt-adapter.timer
  vactive research-prompt-adapter.timer
  vtimer_scheduled research-prompt-adapter.timer  # wq#4910: vactive also passes SubState=elapsed
  vflag "$BIN" --dry-run
  vflag "$BIN" --max-per-run
  vcontains /home/lumen/minerva/context/fleet_tools.yaml "name: research-prompt-adapter"
  vdone
fi

# This bundle owns the unit and timer only; the binary comes from the lumen-bin PR (wq#4677).
[ -x "$BIN" ] || { echo "missing $BIN (land the lumen-bin PR for wq#4677 first)"; exit 1; }
"$BIN" --help 2>&1 | grep -q -- --dry-run || { echo "installed $BIN has no --dry-run; wrong build"; exit 1; }
cmp -s "$HERE/research-prompt-adapter" "$BIN" || echo "NOTE: $BIN differs from the bundle copy (ok if bin moved on by PR)"

sudo install -m 0644 "$HERE/research-prompt-adapter.service" /etc/systemd/system/research-prompt-adapter.service
sudo install -m 0644 "$HERE/research-prompt-adapter.timer"   /etc/systemd/system/research-prompt-adapter.timer
sudo systemctl daemon-reload
sudo systemctl enable --now research-prompt-adapter.timer

echo "--- verify ---"
systemctl list-timers research-prompt-adapter.timer --no-pager --no-legend
echo "manual run: sudo systemctl start research-prompt-adapter.service ; journalctl -u research-prompt-adapter.service -n 30 --no-pager"
echo "dry run:    $BIN --dry-run"
echo "rollback:   sudo systemctl disable --now research-prompt-adapter.timer; sudo rm /etc/systemd/system/research-prompt-adapter.{service,timer}; sudo systemctl daemon-reload"
