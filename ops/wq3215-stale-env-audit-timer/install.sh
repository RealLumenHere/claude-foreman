#!/usr/bin/env bash
# wq#3215 lumen-stale-env-audit install. Run as a user with sudo on Lumen:
#   bash /home/lumen/foreman/ops/wq3215-stale-env-audit-timer/install.sh
# Or from a sandboxed worker: lumen-run start --root -- bash <this file>
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# wq#1696 deploy-verify contract. Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vunit_file "$HERE/lumen-stale-env-audit.service" /etc/systemd/system/lumen-stale-env-audit.service
  vunit_file "$HERE/lumen-stale-env-audit.timer"   /etc/systemd/system/lumen-stale-env-audit.timer
  venabled lumen-stale-env-audit.timer
  vactive lumen-stale-env-audit.timer
  # The unit's ExecStart flags have to exist in the binary it calls. wq#3228 found the
  # opposite case live, where a byte-identical caller gated on flags the callee lacked.
  vflag /home/lumen/bin/stale-env-audit --aq
  vflag /home/lumen/bin/stale-env-audit --docker
  vdone
fi

# The binary is installed by ops/wq3215-stale-env-audit-wire (wq#3245); this bundle owns the
# unit and timer only. --docker in the ExecStart below needs that bundle's build, not d35e196.
[ -x /home/lumen/bin/stale-env-audit ] || { echo "missing /home/lumen/bin/stale-env-audit (install ops/wq3215-stale-env-audit-wire first)"; exit 1; }
/home/lumen/bin/stale-env-audit --help 2>&1 | grep -q -- --docker || {
  echo "installed stale-env-audit has no --docker; install ops/wq3215-stale-env-audit-wire first"; exit 1; }
sudo install -m 0644 "$HERE/lumen-stale-env-audit.service" /etc/systemd/system/lumen-stale-env-audit.service
sudo install -m 0644 "$HERE/lumen-stale-env-audit.timer" /etc/systemd/system/lumen-stale-env-audit.timer
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-stale-env-audit.timer

echo "--- verify ---"
systemctl list-timers lumen-stale-env-audit.timer --no-pager --no-legend
echo "manual run: sudo systemctl start lumen-stale-env-audit.service ; journalctl -u lumen-stale-env-audit.service -n 30 --no-pager"
echo "rollback:   sudo systemctl disable --now lumen-stale-env-audit.timer; sudo rm /etc/systemd/system/lumen-stale-env-audit.{service,timer}; sudo systemctl daemon-reload"
