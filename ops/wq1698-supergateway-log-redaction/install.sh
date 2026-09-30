#!/usr/bin/env bash
# wq#1696 deploy-verify entrypoint for this bundle. This directory installs via
# two separate root-run scripts because they land at different times against
# different preconditions (wq#2546 depends on wq#1698 already being live):
#   apply.sh       — wq#1698 base fix: split stdout/stderr off journald, cap
#                    the local body/chatter file with logrotate.
#   apply-mask.sh  — wq#2546 follow-up: pipe that body/chatter stream through
#                    mask-secrets before it lands, so a credential never
#                    reaches the file in the clear.
# liveness.py's ops_verified() and ops-deploy-drift look for a literal
# install.sh with a --verify handler (ops/VERIFY_CONTRACT.md, wq#1696); this
# file only adds that handler, it does not install anything itself — running
# either apply script still requires root and a quiet window, same as before.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DROPIN_DIR=/etc/systemd/system/desktop-commander-mcp.service.d

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile   "$SRC/no-body-logs-journald.conf"   "$DROPIN_DIR/no-body-logs-journald.conf"
  vfile   "$SRC/supergateway-logrotate.conf"  /etc/logrotate.d/supergateway
  vfile   "$SRC/wq2546-mask-body-log.conf"    "$DROPIN_DIR/wq2546-mask-body-log.conf"
  vactive desktop-commander-mcp.service
  vdone
fi

echo "This bundle installs via two root-run scripts, in order:"
echo "  1. $SRC/apply.sh       (wq#1698 base fix)"
echo "  2. $SRC/apply-mask.sh  (wq#2546 masking follow-up, needs #1 live first)"
echo "Check current deploy state with: bash $SRC/install.sh --verify"
