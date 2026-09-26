#!/usr/bin/env bash
# wq#2806 install: the widened mask-secrets (the wq#2316 bundle is the source of truth
# and its install.sh does the copy — this wrapper adds the wq#2806 backup name and the
# log-pipe check), then report whether the supergateway pipe is running the new rules.
#
# Run from a session NOT under the worker guard (orchestrator, Frank, lumen-run):
#   bash /home/lumen/foreman/ops/wq2806-config-shape-mask/install.sh
# --verify (wq#1696 contract): read-only; bin/ matches the bundle AND the pipe is CURRENT.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
BUNDLE="$SRC/../wq2316-mask-secrets-boundary"
DST=/home/lumen/bin/mask-secrets

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$BUNDLE/mask-secrets" "$DST"
  bash "$SRC/log-pipe-check.sh" || _vmiss "supergateway log pipe is not running the on-disk mask-secrets"
  vdone
fi

if [ -e "$DST" ] && ! cmp -s "$BUNDLE/mask-secrets" "$DST"; then
  cp "$DST" "$DST.pre-wq2806"
  echo "previous version kept at $DST.pre-wq2806"
fi
bash "$BUNDLE/install.sh"
echo "gate: printf '<password>%s</password>\n' hunter2hunter2 | $DST   # must print <password>MASKED</password>"
bash "$SRC/log-pipe-check.sh" || echo "NEXT: sudo systemd-run --on-active=5 --unit=dc-mask-reload systemctl restart desktop-commander-mcp   (detached; never foreground — you ride this unit)"
