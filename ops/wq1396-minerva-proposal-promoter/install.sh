#!/usr/bin/env bash
# Install minerva-proposal-promoter (wq#1396): script -> /home/lumen/bin, units -> systemd.
# Idempotent: re-running converges to the same state.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# wq#1500 (2026-09-13): superseded by minerva_promote.py (minerva repo) + ops/wq1500-minerva-promoter/.
# Different column/marker/lane and no [SEC] prefix -> running both double-files every CVE proposal.
if [ "${WQ1396_FORCE_LEGACY:-0}" != "1" ]; then
  echo "refuse: wq#1396 promoter is superseded by ops/wq1500-minerva-promoter/install.sh (see README)" >&2
  exit 2
fi

install -m 0755 "$HERE/minerva_proposal_promoter.py" /home/lumen/bin/minerva-proposal-promoter
sudo install -m 0644 "$HERE/minerva-proposal-promoter.service" /etc/systemd/system/minerva-proposal-promoter.service
sudo install -m 0644 "$HERE/minerva-proposal-promoter.timer" /etc/systemd/system/minerva-proposal-promoter.timer
sudo systemctl daemon-reload
sudo systemctl enable --now minerva-proposal-promoter.timer
systemctl list-timers minerva-proposal-promoter.timer --no-pager --no-legend
