#!/usr/bin/env bash
# Install litellm-auth-alert (wq#911): script -> /home/lumen/bin, units -> systemd.
# Idempotent: re-running converges to the same state.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

install -m 0755 "$HERE/litellm-auth-alert" /home/lumen/bin/litellm-auth-alert
sudo install -m 0644 "$HERE/litellm-auth-alert.service" /etc/systemd/system/litellm-auth-alert.service
sudo install -m 0644 "$HERE/litellm-auth-alert.timer" /etc/systemd/system/litellm-auth-alert.timer
sudo systemctl daemon-reload
sudo systemctl enable --now litellm-auth-alert.timer
systemctl list-timers litellm-auth-alert.timer --no-pager --no-legend
