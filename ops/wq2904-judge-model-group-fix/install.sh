#!/usr/bin/env bash
# wq#2904 — register wq2821-judge-llama8b in the live LiteLLM gateway config.
#
# No automated install step: this is a hand-merge into
# /home/lumen/agent/litellm_config.yaml outside the "# BEGIN/END GENERATED
# inference_nodes.yaml" sentinel block, same convention as
# wq#897/wq#911/wq#948/wq#1295/wq#1406/wq#1590/wq#2627 (see README.md "Why
# the live config isn't touched directly" — no worker sandbox in this repo
# can write /home/lumen/agent or restart the gateway container). See
# README.md "Deploy checklist" for the manual merge + systemd-run restart +
# acceptance-probe sequence.
#
# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any
# install step (there is none here beyond the manual checklist above).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
LIVE_CONFIG=/home/lumen/agent/litellm_config.yaml

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vcontains "$LIVE_CONFIG" 'model_name: wq2821-judge-llama8b'
  vdone
fi

cat <<'EOF'
This bundle has no automated install step -- see README.md "Deploy
checklist" for the manual merge + systemd-run restart + acceptance-probe
sequence (python3 judge/judge_bench.py --models wq2821-judge-llama8b).
EOF
