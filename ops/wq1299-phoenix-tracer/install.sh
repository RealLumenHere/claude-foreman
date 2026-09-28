#!/usr/bin/env bash
# Install the Phoenix tracer (wq#1299): container unit + disk guard.
# Idempotent: re-running converges to the same state.
#
# Not run by this BUILD worker — the sandbox has no docker/systemctl write
# access (guard.sh denies docker beyond ps/logs, and /etc is outside the
# worktree). Run this from an interactive/root session on the gateway node.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
LIVE_CONFIG=/home/lumen/agent/litellm_config.yaml

# RETIRED 2026-09-28 (wq#2985, triage of wq#1732's 36 untracked PENDING bundles).
#
# This tracer exports spans through a LiteLLM OTLP callback. The live gateway
# config has zero otel/otlp/phoenix references and nine langfuse ones, and
# langfuse-evaluator, -smoke and -idle-alert are all enabled+active. The
# LLM-observability path this was written to provide has been served by Langfuse
# for the whole 618h this bundle sat unshipped, so deploying it now would add a
# second tracer to the gateway rather than fill a gap.
#
# Kept rather than deleted so the refusal below can stop a future blind deploy
# off an ABSENT verdict -- which is exactly how this bundle reached wq#2985.
# To revive it, establish that Langfuse no longer covers the need, then set
# WQ1299_FORCE_RETIRED_DEPLOY=1.

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
# For a retired bundle "correct" is inverted: nothing of ours should be live,
# and the path that replaced it should be.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  for u in phoenix.service phoenix-disk-guard.service phoenix-disk-guard.timer; do
    if [ -e "/etc/systemd/system/$u" ]; then
      _vmiss "retired unit present: /etc/systemd/system/$u (wq#2985)"
    fi
  done
  venabled langfuse-evaluator.timer
  vcontains "$LIVE_CONFIG" 'langfuse'
  vdone
fi

if [ "${WQ1299_FORCE_RETIRED_DEPLOY:-0}" != "1" ]; then
  echo "refuse: wq#1299 phoenix-tracer was RETIRED 2026-09-28 (wq#2985) -- the gateway's" >&2
  echo "        live observability path is Langfuse. See the header for how to revive." >&2
  exit 2
fi

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
