#!/usr/bin/env bash
# Install the minerva-promote backfill timer (wq#1500). Idempotent: re-running converges.
# The promoter CODE is not installed from here — it lives in the minerva repo
# (/home/lumen/minerva/minerva_promote.py, symlinked as /home/lumen/bin/minerva-promote); this
# only schedules its `run` backfill and retires the wq#1396 predecessor if it is still enabled.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
LIVE=/home/lumen/minerva/minerva_promote.py

if [ ! -f "$LIVE" ]; then
  echo "refuse: $LIVE missing — land the promoter in the minerva repo first (design §2a)" >&2
  exit 2
fi
if ! grep -q '^def run(' "$LIVE" || ! grep -q "MARKER = 'minerva_proposal='" "$LIVE"; then
  echo "refuse: $LIVE is not the wq#1500 promoter (no run()/marker) — not scheduling it" >&2
  exit 2
fi

# wq#1396 predecessor: different column (proposals.ticket), different marker, build lane, no
# [SEC] title prefix -> would double-file every CVE proposal next to the wq#1500 ticket.
if systemctl list-unit-files minerva-proposal-promoter.timer --no-legend 2>/dev/null | grep -q minerva-proposal-promoter; then
  echo "retiring wq#1396 minerva-proposal-promoter.timer (superseded by minerva-promote.timer)"
  sudo systemctl disable --now minerva-proposal-promoter.timer || true
fi

sudo install -m 0644 "$HERE/minerva-promote.service" /etc/systemd/system/minerva-promote.service
sudo install -m 0644 "$HERE/minerva-promote.timer" /etc/systemd/system/minerva-promote.timer
sudo systemctl daemon-reload
sudo systemctl enable --now minerva-promote.timer
systemctl list-timers minerva-promote.timer --no-pager --no-legend
python3 "$LIVE" status
