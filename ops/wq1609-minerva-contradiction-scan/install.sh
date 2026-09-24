#!/usr/bin/env bash
# Install lumen-contradiction-scan (wq#1609): script -> /home/lumen/bin, units -> systemd.
# Idempotent: re-running converges to the same state.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vfile      "$HERE/lumen_contradiction_scan.py"     /home/lumen/bin/lumen-contradiction-scan
  vunit_file "$HERE/lumen-contradiction-scan.service" /etc/systemd/system/lumen-contradiction-scan.service
  vunit_file "$HERE/lumen-contradiction-scan.timer"   /etc/systemd/system/lumen-contradiction-scan.timer
  venabled   lumen-contradiction-scan.timer
  vactive    lumen-contradiction-scan.timer
  vdone
fi

python3 -m pytest -q "$HERE/test_lumen_contradiction_scan.py"

install -m 0755 "$HERE/lumen_contradiction_scan.py" /home/lumen/bin/lumen-contradiction-scan

# wq#2411: a build worker has no sudo, so the systemd steps below always abort the
# script — but the scanner script itself is user-owned and needs no privilege to
# redeploy. Without this exit, a python-only fix (e.g. wq#1782) lands in the repo
# and never reaches the live timer until a human re-runs the full install, letting
# the timer refile already-fixed contradictions as new tickets (wq#2411 was one).
if [ "${1:-}" = --script-only ]; then
  exit 0
fi

sudo install -m 0644 "$HERE/lumen-contradiction-scan.service" /etc/systemd/system/lumen-contradiction-scan.service
sudo install -m 0644 "$HERE/lumen-contradiction-scan.timer" /etc/systemd/system/lumen-contradiction-scan.timer
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-contradiction-scan.timer
systemctl is-active --quiet lumen-contradiction-scan.timer
systemctl list-timers lumen-contradiction-scan.timer --no-pager --no-legend

/home/lumen/bin/lumen-contradiction-scan
