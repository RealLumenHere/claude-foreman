#!/usr/bin/env bash
# wq#3097 — LiteLLM GHSA-3cv6-jpf6-8222 (CVE-2026-84377, Moderate) patch prep. See README.md.
#
# This bundle ships no install step of its own: the actual fix (TARGET_DIGEST/
# FALLBACK_DIGEST bumped to v1.96.2/v1.88.6, the reject_url_valued_destination
# fix-marker check added to verify(), and OLD/NEW parameterized in the test
# harness) landed directly in /home/lumen/bin main (c4cb6d7) via a separate PR
# (#187) in that lane repo — see README.md "Status vs. the ticket's TODO before
# queueing gate". --verify below checks those edits are still live there (an
# edit, not a copy, so vcontains rather than vfile — see ops/VERIFY_CONTRACT.md
# template). The window op that actually pulls/runs the new image is a distinct,
# not-yet-queued step (README.md "Next step (not this ticket)") with its own
# future liveness check via `maint-ops/litellm-upgrade --verify` once applied;
# it is deliberately out of scope here, same as this bundle's install step.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
BIN=/home/lumen/bin

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vcontains "$BIN/maint-ops/litellm-upgrade" \
    'TARGET_DIGEST=\$\{TARGET_DIGEST:-sha256:154e23bb5f31b1f10e16392a8ef299bd2cde08de3a64a6849002cfcc25ce3c63\}'
  vcontains "$BIN/maint-ops/litellm-upgrade" \
    'FALLBACK_DIGEST=\$\{FALLBACK_DIGEST:-sha256:0c73da8d132b893bfa037511553bafbc9e2ff6abe9b474a66e0e9a354c0be064\}'
  vcontains "$BIN/maint-ops/litellm-upgrade" 'reject_url_valued_destination'
  vcontains "$BIN/maint-ops/tests/harness_litellm.sh" 'OLD=\$\{OLD:-sha256:'
  vcontains "$BIN/maint-ops/tests/harness_litellm.sh" \
    'NEW=\$\{NEW:-sha256:154e23bb5f31b1f10e16392a8ef299bd2cde08de3a64a6849002cfcc25ce3c63\}'
  vdone
fi

echo "wq3097 ships no install step: the fix already landed in $BIN via PR #187 (see README.md)."
echo "This install.sh exists only to answer --verify per ops/VERIFY_CONTRACT.md (wq#1696/wq#3105)."
exit 0
