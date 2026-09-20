#!/usr/bin/env bash
# wq#897 — nothing to install: sentinel_probes.diff and gatus_config.diff are records of
# edits already applied live to /home/lumen/sentinel (no git there). This script exists
# for the wq#1696 deploy-verify contract: --verify proves the edits are still live and
# that the tracked copy of the gatus diff stays free of the push token (wq#2116).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
SENTINEL=/home/lumen/sentinel

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vcontains "$SENTINEL/probes/sentinel_probes.py" "probe-litellm-auth-monitor"
  vcontains "$SENTINEL/config/config.yaml" "probe-litellm-auth-monitor"
  if grep -Eq 'token: "[^"*]+"' "$SRC/gatus_config.diff"; then
    _vmiss "gatus_config.diff carries an unredacted token: line"
  fi
  vdone
fi

echo "wq#897: no install step — the diffs in $SRC are already applied live (see README.md)."
