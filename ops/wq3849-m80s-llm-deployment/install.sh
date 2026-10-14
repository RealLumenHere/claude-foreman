#!/usr/bin/env bash
# install.sh -- wq#1696 deploy-verify contract handler for wq3849-m80s-llm-deployment
# (added by wq#3859's verify-pass; the bundle shipped in PR#1844 without one).
#
# This bundle's payload is NOT installed from here. install-ollama.sh runs on the
# M80 node itself (privileged/root session); pull-models.sh runs there too, by hand,
# off-peak (wq#1297); apply.sh runs on Lumen in a privileged session once the node's
# real api_base is known. See README.md in this directory for the exact steps and
# who runs each one. No M80 node is network-reachable, or even named, yet:
# lumen_state.py and harness/fleet-exec-policy.yaml track neither (re-checked at
# verify time, 2026-10-14) -- same prerequisite gap as the sibling bundle
# ops/wq3652-m80s-provisioning (wq#3703/3704/3705).
#
# Until one of those closes, there is no live target anywhere to check -- not
# "unreachable", genuinely nonexistent -- so this handler always answers ERR
# (exit 2) on --verify rather than guessing a hostname or reporting a false
# MISS/OK. Per ops/VERIFY_CONTRACT.md's "ABSENT is not a disposition": "I could
# not look" must not read as "it is not deployed". Once wq#3705 lands a real
# hostname, replace this with vremote_up/vremote_file checks against the installed
# Ollama binary/unit and apply.sh's own `verify` subcommand against the live
# litellm_config.yaml aliases (see ops/wq2286-gitea-secret-scan/install.sh for the
# vremote_* pattern).
set -euo pipefail

case "${1:-}" in
  --verify)
    echo "ERR no M80 node registered yet -- see wq#3703/wq#3704/wq#3705 and README.md"
    exit 2
    ;;
  *)
    echo "install.sh: this bundle has no local install step." >&2
    echo "Privileged/root session on the M80 node: run install-ollama.sh, then" >&2
    echo "pull-models.sh off-peak (wq#1297). Privileged session on Lumen: run" >&2
    echo "apply.sh <m80s_api_base> once the node has a real hostname. See README.md." >&2
    exit 1
    ;;
esac
