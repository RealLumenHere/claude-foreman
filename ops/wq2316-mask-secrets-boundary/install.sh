#!/usr/bin/env bash
# wq#2316 mask-secrets word-boundary install.
#
# /home/lumen/bin is a PRIMARY tree: guard.sh denies Write/Edit there for build
# workers (wq#1719/wq#900), which is why this ships as a bundle instead of a direct
# edit. No root is needed — /home/lumen/bin is lumen-owned — but it must be run from
# a session that is NOT under the worker guard (orchestrator, Frank, or lumen-run):
#   bash /home/lumen/foreman/ops/wq2316-mask-secrets-boundary/install.sh
#
# Idempotent. Leaves /home/lumen/bin with one modified tracked file for its owner to
# commit; the bundle is the reviewed source of truth either way.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DST=/home/lumen/bin/mask-secrets

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/mask-secrets" "$DST"
  vdone
fi

if [ -e "$DST" ] && ! cmp -s "$SRC/mask-secrets" "$DST"; then
  cp "$DST" "$DST.pre-wq2316"
  echo "previous version kept at $DST.pre-wq2316"
fi

cp "$SRC/mask-secrets" "$DST"
chmod 755 "$DST"

echo "installed $DST"
echo "gate:  echo 'repo highs_solver' | $DST      # must print unchanged"
echo "tests: python3 -m pytest $SRC/test_mask_secrets_boundary.py -q"
