#!/usr/bin/env bash
# wq#4258 Phase 2 — register claude-sonnet / claude-haiku in the live LiteLLM
# gateway config.
#
# No automated install step: this is a hand-merge of litellm_config_snippet.yaml
# into /home/lumen/agent/litellm_config.yaml, same convention as
# wq#897/wq#911/wq#948/wq#1295/wq#1406/wq#1590/wq#2627/wq#2904 (see README.md
# "Phase 2" — no worker sandbox in this repo can write /home/lumen/agent or
# restart the gateway container, and the merge is additionally blocked on the
# ANTHROPIC_API_KEY value landing in fleet-config/docker/litellm/.env, a
# credential mint only Frank can do). See README.md for the deploy sequence
# once the key exists.
#
# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any
# install step (there is none here beyond the manual checklist in README.md).
# WQ4258_LIVE_CONFIG exists so tests can point this at a fixture instead of
# the real gateway config.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
LIVE_CONFIG="${WQ4258_LIVE_CONFIG:-/home/lumen/agent/litellm_config.yaml}"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vcontains "$LIVE_CONFIG" 'model_name: claude-sonnet'
  vcontains "$LIVE_CONFIG" 'model_name: claude-haiku'
  # wq#4260: a MISS/FAIL here is the expected state until ANTHROPIC_API_KEY
  # lands (Frank-only credential mint, README.md "Phase 2") -- it is NOT a
  # signal that this script needs to be (re-)run as root/sudo. There is no
  # install step for --verify to have skipped; read README.md before
  # redispatching this as a privileged-install ticket.
  echo "NOTE: FAIL here means the key hasn't landed yet, not that install needs root -- see README.md Phase 2."
  vdone
fi

cat <<'EOF'
This bundle has no automated install step -- see README.md "Phase 2" for the
manual merge (litellm_config_snippet.yaml) + container restart + acceptance
probe (curl http://127.0.0.1:4000/health/liveliness, then one real completion
against each new alias) once ANTHROPIC_API_KEY lands.
EOF
