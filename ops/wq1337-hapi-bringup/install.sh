#!/bin/bash
# wq#1337 — hapi-1 (BC-250) bring-up bundle. Landed by wq#3706 for the sole purpose of
# answering --verify (ops/VERIFY_CONTRACT.md, wq#1696); there is no automated install.
#
# Bring-up is hands-on and hardware-gated: the card is on the bench awaiting thermal pads
# (`lumen_state current hapi-1` -> hapi-1.bringup_eta), the BIOS flash needs a programmer
# clipped to the chip, and the payload lands on hapi-1, never on Lumen. A Lumen-local
# check would report this bundle absent however live it became, which is the
# VERIFY_CONTRACT "Bundles that do not install onto Lumen" case -- so every check here
# asks hapi-1, gated on vremote_up, and an unreachable hapi-1 is ERR (exit 2), never MISS.
# "I could not look" must not read as "it is not deployed" (wq#2985). Until the host
# exists that ERR is the correct standing verdict, and ops-deploy-drift opens aq rows only
# on VERIFY_FAIL, so it costs no alert noise.
#
#   install.sh --verify   read-only liveness check against hapi-1
#   install.sh            refuses; follow RUNBOOK.md by hand
#
# HAPI1_HOST overrides the ssh target for the day the host exists under another
# name/address (look it up with lumen_state, never assume -- instinct 1).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
HOST="${HAPI1_HOST:-hapi-1}"
UNIT=llama-server.service

case "${1:-}" in
  --verify)
    . "$SRC/../_verify.sh"
    if [ "$(hostname -s)" = "$HOST" ]; then
      vfile "$SRC/$UNIT" "/etc/systemd/system/$UNIT"
      vactive "$UNIT"
      venabled "$UNIT"
    else
      vremote_up "$HOST" || vdone
      vremote_file "$HOST" "$SRC/$UNIT" "/etc/systemd/system/$UNIT"
      vremote_unit "$HOST" "$UNIT"
    fi
    vdone ;;
esac

# bench.sh is a bench tool run by hand from this directory, not an installed artifact, and
# litellm-hapi1-dark.yaml is deliberately dark until wq#266's eval gate passes
# (RUNBOOK.md section 10) -- neither is checked above, on purpose.
echo "No automated install: hapi-1 bring-up is a manual, hands-on procedure." >&2
echo "Follow RUNBOOK.md in this directory, plus wq#3651's RUNBOOK-ADDENDUM.md, on the" >&2
echo "box once the physical bring-up is done." >&2
exit 1
