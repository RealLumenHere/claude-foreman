#!/usr/bin/env bash
# Install wq#1406's paid-spend-tripwire timer. Idempotent: re-running
# converges to the same state. Run from a root/interactive session on the
# gateway node — this BUILD worker's sandbox cannot install systemd units
# or reach the live LiteLLM config (see README.md).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

sudo install -m 0644 "$HERE/paid-spend-tripwire.service" /etc/systemd/system/paid-spend-tripwire.service
sudo install -m 0644 "$HERE/paid-spend-tripwire.timer" /etc/systemd/system/paid-spend-tripwire.timer
sudo systemctl daemon-reload
sudo systemctl enable --now paid-spend-tripwire.timer
systemctl list-timers paid-spend-tripwire.timer --no-pager --no-legend

echo
echo "Remaining manual steps (see README.md 'Deploy checklist'):"
echo "  1. python3 $HERE/audit_fallback_chains.py /home/lumen/agent/litellm_config.yaml"
echo "  2. Fix any VIOLATION lines (reorder free-before-paid in router_settings.fallbacks)"
echo "  3. Merge $HERE/litellm_config_snippet.yaml into the live proxy config"
echo "  4. Deploy paid_fallback_alert.py onto the proxy's PYTHONPATH; restart the proxy"
