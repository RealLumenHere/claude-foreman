#!/usr/bin/env bash
# Install litellm-completion-probe (wq#3169): script -> /home/lumen/bin,
# units -> systemd. Idempotent: re-running converges to the same state.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vfile "$HERE/litellm-completion-probe" /home/lumen/bin/litellm-completion-probe
  vunit_file "$HERE/litellm-completion-probe.service" /etc/systemd/system/litellm-completion-probe.service
  vunit_file "$HERE/litellm-completion-probe.timer" /etc/systemd/system/litellm-completion-probe.timer
  venabled litellm-completion-probe.timer
  vactive litellm-completion-probe.timer
  vdone
fi

install -m 0755 "$HERE/litellm-completion-probe" /home/lumen/bin/litellm-completion-probe
sudo install -m 0644 "$HERE/litellm-completion-probe.service" /etc/systemd/system/litellm-completion-probe.service
sudo install -m 0644 "$HERE/litellm-completion-probe.timer" /etc/systemd/system/litellm-completion-probe.timer
sudo systemctl daemon-reload
sudo systemctl enable --now litellm-completion-probe.timer
systemctl list-timers litellm-completion-probe.timer --no-pager --no-legend
