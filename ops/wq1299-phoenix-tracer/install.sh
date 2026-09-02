#!/usr/bin/env bash
# Install the Phoenix tracer (wq#1299): container unit + disk guard.
# Idempotent: re-running converges to the same state.
#
# Not run by this BUILD worker — the sandbox has no docker/systemctl write
# access (guard.sh denies docker beyond ps/logs, and /etc is outside the
# worktree). Run this from an interactive/root session on the gateway node.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

mkdir -p /home/lumen/var/phoenix

sudo install -m 0644 "$HERE/phoenix.service" /etc/systemd/system/phoenix.service
sudo install -m 0644 "$HERE/phoenix-disk-guard.service" /etc/systemd/system/phoenix-disk-guard.service
sudo install -m 0644 "$HERE/phoenix-disk-guard.timer" /etc/systemd/system/phoenix-disk-guard.timer
sudo systemctl daemon-reload
sudo systemctl enable --now phoenix.service
sudo systemctl enable --now phoenix-disk-guard.timer

echo "--- verify ---"
systemctl is-active phoenix.service
systemctl list-timers phoenix-disk-guard.timer --no-pager --no-legend
echo "Now: merge litellm_config_snippet.yaml into the live LiteLLM proxy"
echo "config.yaml, deploy litellm_phoenix_sampler.py onto its PYTHONPATH,"
echo "and restart the litellm proxy for the callback to load."
