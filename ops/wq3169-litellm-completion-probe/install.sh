#!/usr/bin/env bash
# Install litellm-completion-probe (wq#3169): script -> /home/lumen/bin,
# units -> systemd. Idempotent: re-running converges to the same state.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

install -m 0755 "$HERE/litellm-completion-probe" /home/lumen/bin/litellm-completion-probe
sudo install -m 0644 "$HERE/litellm-completion-probe.service" /etc/systemd/system/litellm-completion-probe.service
sudo install -m 0644 "$HERE/litellm-completion-probe.timer" /etc/systemd/system/litellm-completion-probe.timer
sudo systemctl daemon-reload
sudo systemctl enable --now litellm-completion-probe.timer
systemctl list-timers litellm-completion-probe.timer --no-pager --no-legend
