#!/bin/bash
# wq#3181: --verify handler for wq#3168 (ops/VERIFY_CONTRACT.md, wq#1696). The applied
# part of this fix — retiring the dead claude-max-opus rung and closing the
# chairman-list bug that let a disabled seat still eat a real timeout in
# synthesize() — lives in tools/council/council.yaml and tools/council/council.py in
# this same repo, so "live" means merged to primary main, not deployed anywhere else.
# The kimik3-0.4b-local / lumen-lfm2.5-2.6b / claude-max-sonnet retirements documented
# in README.md are NOT covered here: they need an edit to /home/lumen/agent/litellm_config.yaml
# on the gateway node, which no sandbox that built this bundle had access to, so nothing
# was shipped for them to verify. No install step: the fix is already merged code, not a
# copied artifact.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vcontains "$SRC/../../tools/council/council.yaml" \
    'claude-max-opus,[[:space:]]*timeout_s: 135,[[:space:]]*enabled: false'
  vcontains "$SRC/../../tools/council/council.py" \
    'wq#3168: a chairman entry for a disabled/retired seat'
  vcontains "$SRC/../../tools/council/tests/test_council.py" \
    'test_load_config_drops_disabled_seat_from_chairman_list'
  vdone
fi

echo "nothing to install: ${0##*/} verifies an already-merged in-repo fix, no artifact to copy"
