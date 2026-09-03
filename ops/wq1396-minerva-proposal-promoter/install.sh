#!/usr/bin/env bash
# Install minerva-proposal-promoter (wq#1396): script -> /home/lumen/bin, units -> systemd.
# Idempotent: re-running converges to the same state.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

install -m 0755 "$HERE/minerva_proposal_promoter.py" /home/lumen/bin/minerva-proposal-promoter
sudo install -m 0644 "$HERE/minerva-proposal-promoter.service" /etc/systemd/system/minerva-proposal-promoter.service
sudo install -m 0644 "$HERE/minerva-proposal-promoter.timer" /etc/systemd/system/minerva-proposal-promoter.timer
sudo systemctl daemon-reload
sudo systemctl enable --now minerva-proposal-promoter.timer
systemctl list-timers minerva-proposal-promoter.timer --no-pager --no-legend
