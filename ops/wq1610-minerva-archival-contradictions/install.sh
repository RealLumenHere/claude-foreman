#!/usr/bin/env bash
# Install note-contradiction-scan (wq#1610): script -> /home/lumen/bin, units -> systemd.
# Idempotent: re-running converges to the same state.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

install -m 0755 "$HERE/note_contradiction_scan.py" /home/lumen/bin/minerva-archival-contradictions
sudo install -m 0644 "$HERE/minerva-archival-contradictions.service" /etc/systemd/system/minerva-archival-contradictions.service
sudo install -m 0644 "$HERE/minerva-archival-contradictions.timer" /etc/systemd/system/minerva-archival-contradictions.timer
sudo systemctl daemon-reload
sudo systemctl enable --now minerva-archival-contradictions.timer
systemctl list-timers minerva-archival-contradictions.timer --no-pager --no-legend
