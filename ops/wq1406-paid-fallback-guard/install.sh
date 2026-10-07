#!/usr/bin/env bash
# Install wq#1406's paid-spend-tripwire timer. Idempotent: re-running
# converges to the same state. Run from a root/interactive session on the
# gateway node — this BUILD worker's sandbox cannot install systemd units
# or reach the live LiteLLM config (see README.md).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# RETIRED 2026-10-07 (wq#3486, root --verify sweep of closed-but-undeployed bundles).
#
# Two reasons, either one enough:
#  1. paid_spend_tripwire.py:129 reads the master key positionally
#     (read_text().split("=", 1)[1]). That is the exact bug wq#1747/wq#2902 fixed in
#     litellm-budget-alert: once litellm-master.env holds a second line, the value
#     carries the next secret into the Authorization header and any exception text
#     leaks both to journal + ntfy. Deploying it as shipped reopens that leak.
#  2. The drain it guards is already paged by live timers: lumen-budget-alert.timer
#     (per-service LiteLLM key budgets at 80%, wq#719) and lumen-deepseek-spend.timer
#     (spend-alert.py: %-of-budget thresholds + 2x-7d runaway burn, wq#720). The other
#     half (paid_fallback_alert callback) needs a gateway config merge + proxy restart
#     the bundle never staged.
#
# Kept rather than deleted so the refusal below stops a future blind deploy off an
# ABSENT verdict. To revive: port the key read to lumen_env.env_value, show a paid
# model the two live timers miss, then set WQ1406_FORCE_RETIRED_DEPLOY=1.

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
# Inverted for a retired bundle: our units stay absent, the replacements stay live.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  for u in paid-spend-tripwire.service paid-spend-tripwire.timer; do
    if [ -e "/etc/systemd/system/$u" ]; then
      _vmiss "retired unit present: /etc/systemd/system/$u (wq#3486)"
    fi
  done
  venabled lumen-budget-alert.timer
  vactive  lumen-budget-alert.timer
  venabled lumen-deepseek-spend.timer
  vactive  lumen-deepseek-spend.timer
  vdone
fi

if [ "${WQ1406_FORCE_RETIRED_DEPLOY:-0}" != "1" ]; then
  echo "refuse: wq#1406 paid-spend-tripwire was RETIRED 2026-10-07 (wq#3486) -- positional" >&2
  echo "        master-key read (wq#2902 leak class); lumen-budget-alert + spend-alert cover it." >&2
  exit 2
fi

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
