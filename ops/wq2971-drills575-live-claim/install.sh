#!/usr/bin/env bash
# wq#2971 deploy-verify shim. `v4/probes/drills_575.py` is a minerva-tracked file,
# not a foreman one -- the real fix ships through minerva's own PR flow (PR #48,
# commit c4d26aa / minerva main 6c3483f), and this bundle exists only to give the
# fix a citable copy + a --verify handler (ops/VERIFY_CONTRACT.md, wq#1696). There
# is nothing for this script to install.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
MINERVA=/home/lumen/minerva

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
#
# Not a byte-identity check: drills_575.py is a shared probe file that later
# tickets keep patching in place (wq#3019's orphan_check scoping landed on top
# of this fix the same day, minerva commit eaa7d78) -- a `vfile` comparison
# against this bundle's snapshot would start reporting MISS the moment any
# later, unrelated patch to the same file lands. Check for this fix's own
# markers instead: the throwaway-ticket minter, the dynamic SRC it derives,
# and the release-at-exit that returns the ticket when the run ends.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vcontains "$MINERVA/v4/probes/drills_575.py" 'def _mint_ticket'
  vcontains "$MINERVA/v4/probes/drills_575.py" 'SRC = "wq#%s" % SRC_TICKET'
  vcontains "$MINERVA/v4/probes/drills_575.py" '"release", SRC_TICKET'
  vdone
fi

echo "wq#2971 ships through minerva's own PR flow (PR #48) -- nothing to install"
echo "from this bundle. $SRC/drills_575.py is a citable mirror only."
exit 0
