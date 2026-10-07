#!/bin/bash
# wq#2763: --verify handler for wq#2740 (ops/VERIFY_CONTRACT.md, wq#1696). This bundle
# ships reproduction probes for the account() prefix-adjacency fix (rotation row
# sha256:a745825001bc); the fix itself lives in harness/mask_replay.py in this same
# repo, so "live" means merged to primary main, not deployed anywhere else. No install
# step: the probes run in place from $SRC.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  # wq#3468: the fix's code, not its docstring wording; b153eda2 rewrapped the docstring
  # and broke the old prose marker while the rule itself (now _needs_prefix) stayed live.
  vcontains "$SRC/../../harness/mask_replay.py" \
    'return len\(value\) < TRUNCATED_FLOOR'
  vcontains "$SRC/../../harness/tests/test_mask_replay.py" \
    'test_a_short_body_is_only_accounted_with_its_prefix_in_front'
  vcontains "$SRC/../../harness/tests/test_mask_replay.py" \
    'test_a_body_at_the_floor_still_accounts_without_its_prefix'
  vdone
fi

echo "nothing to install: ${0##*/} ships reproduction probes only, run in place from $SRC"
