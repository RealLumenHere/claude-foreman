#!/usr/bin/env bash
# wq#2172 — nothing to install. This bundle is a probe: is the deployed lumen-mailbot
# worker at or past the last mailbot commit that touches a deploy input?
#   install.sh --verify   exit 0 CURRENT, 1 STALE (MISS line), 2 could not check
# The Cloudflare token comes from $CLOUDFLARE_API_TOKEN, else the file named by
# $WQ2172_TOKEN_FILE — check_live.py reads it (and refuses one that is group/world-readable),
# so this wrapper never touches the value. Fix for STALE: /home/lumen/mailbot/deploy.sh.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
CHECK="${WQ2172_CHECK_LIVE:-$SRC/check_live.py}"  # overridable so tests never reach Cloudflare

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  rc=0
  out="$(python3 "$CHECK" 2>&1)" || rc=$?
  case "$rc" in
    0) echo "$out" ;;
    1) _vmiss "$(printf '%s' "$out" | head -n1)" ;;
    *) echo "ERR $(printf '%s' "$out" | head -n1)"; _V_ERR=1 ;;
  esac
  vdone
fi

echo "wq2172-mailbot-liveness has nothing to install; run: $0 --verify" >&2
exit 2
