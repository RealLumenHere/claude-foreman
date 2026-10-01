#!/usr/bin/env bash
# wq#3321 — remove the dead chrome-mcp.lumennode.uk cloudflared ingress
# entry so ui-drift's ORIGIN_DOWN check stops firing on a service that was
# deliberately stopped+disabled (wq#2751/wq#2768 RCE-surface shutdown,
# 2026-09-29T20:12:55Z). See RUNBOOK.md for the full reconstruction.
#
# Needs root (writes /etc/cloudflared/config.yml, restarts cloudflared) and
# a grant on /etc/cloudflared/ — the build worker that staged this bundle
# had neither, so `--apply` below is a manual step for whoever holds that
# access, not something this script runs unattended.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any
# install step. Reuses remove_chrome_mcp_ingress.py --dry-run (itself
# read-only) for the live check instead of re-parsing config.yml here, so
# the probe and the installer can't drift apart (wq#3228 lesson).
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  out="$(python3 "$SRC/remove_chrome_mcp_ingress.py" --dry-run 2>&1)" || {
    echo "ERR remove_chrome_mcp_ingress.py --dry-run failed: $out"
    _V_ERR=1
  }
  case "$out" in
    "no ingress entry for chrome-mcp.lumennode.uk"*) ;;
    "found: "*) _vmiss "cloudflared still serves chrome-mcp.lumennode.uk: $out" ;;
    *) echo "ERR unexpected dry-run output: $out"; _V_ERR=1 ;;
  esac
  vdone
fi

echo "== apply (needs root + /etc/cloudflared/ access) =="
python3 "$SRC/remove_chrome_mcp_ingress.py" --apply
