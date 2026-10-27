#!/usr/bin/env bash
# wq#4695 — stop lumen_watchdog.py and the maint-ops ollama/tailscale canary
# harnesses from targeting retired nodes (isis/hathor/khepri/wadjet), and add
# a single shared retired-nodes list both consumers check.
#
# No automated install step: /home/lumen/agent and /home/lumen/bin are both
# worker-frozen PRIMARY trees (guard.sh wq#900/wq#1719) — no worker sandbox in
# this repo can write either path directly, same constraint as every prior
# litellm_config.yaml ticket (wq#897/#911/#948/#1295/#1406/#1590/#2627). See
# README.md "Deploy checklist" for the manual copy + verify sequence.
#
# wq#4701: lumen_watchdog.py is retired (ops/wq4701-lumen-watchdog-retire/),
# so this bundle no longer ships or verifies it. Last shipped copy: 7e91411f.
#
# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any
# install step (there is none here beyond the manual checklist below).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

RETIRED_JSON_LIVE=/home/lumen/bin/fleet-retired-nodes.json
OLLAMA_CANARY_LIVE=/home/lumen/bin/maint-ops/tests/ollama_canary.sh
TAILSCALE_CANARY_LIVE=/home/lumen/bin/maint-ops/tests/tailscale_canary.sh

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/fleet-retired-nodes.json" "$RETIRED_JSON_LIVE"
  vfile "$SRC/tests/ollama_canary.sh" "$OLLAMA_CANARY_LIVE"
  vfile "$SRC/tests/tailscale_canary.sh" "$TAILSCALE_CANARY_LIVE"
  vdone
fi

cat <<EOF
This bundle has no automated install step -- see README.md "Deploy checklist"
for the manual copy sequence. Summary:
  cp $SRC/fleet-retired-nodes.json  $RETIRED_JSON_LIVE
  cp $SRC/tests/ollama_canary.sh    $OLLAMA_CANARY_LIVE
  cp $SRC/tests/tailscale_canary.sh $TAILSCALE_CANARY_LIVE
Then run: $0 --verify
EOF
