#!/usr/bin/env bash
# install.sh -- wq#1696 deploy-verify contract handler for wq3652-m80s-provisioning
# (added by wq#3708's verify-pass; the bundle shipped in PR#1732 without one).
#
# This bundle's payload is NOT installed from here. It ships reference files
# (fworker-sandbox@.service, provision-fworker.sh, fworker_sandbox_slot.sh,
# wq-remote, fworker.env.example) for a privileged/console session to copy onto
# an M80s node -- see README.md in this directory for the exact steps. No M80
# node is network-reachable, or even named, yet: lumen_state.py and
# harness/fleet-exec-policy.yaml track neither (re-checked at verify time,
# 2026-10-08). wq#3703/3704/3705 are the queued follow-ups that give it a
# hostname and run provision-fworker.sh.
#
# Until one of those closes, there is no live target anywhere to check -- not
# "unreachable", genuinely nonexistent -- so this handler always answers ERR
# (exit 2) on --verify rather than guessing a hostname or reporting a false
# MISS/OK. Per ops/VERIFY_CONTRACT.md's "ABSENT is not a disposition": "I could
# not look" must not read as "it is not deployed". Once wq#3705 lands a real
# hostname, replace this with vremote_up/vremote_file checks against it
# (see ops/wq2286-gitea-secret-scan/install.sh for the worked pattern).
set -euo pipefail

case "${1:-}" in
  --verify)
    echo "ERR no M80 node registered yet -- see wq#3703/wq#3704/wq#3705 and README.md"
    exit 2
    ;;
  *)
    echo "install.sh: this bundle has no local install step." >&2
    echo "Privileged/console session: run provision-fworker.sh on the M80 node per README.md." >&2
    exit 1
    ;;
esac
