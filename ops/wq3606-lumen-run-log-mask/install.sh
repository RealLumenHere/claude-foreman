#!/usr/bin/env bash
# wq#3606 lumen-run log masking. Run as lumen on Lumen:
#   bash /home/lumen/foreman/ops/wq3606-lumen-run-log-mask/install.sh
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=/home/lumen/bin/lumen-run

# wq#1696 deploy-verify contract. Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vfile "$HERE/lumen-run" "$BIN"
  vdone
fi

bash -n "$HERE/lumen-run"
[ -x /home/lumen/bin/mask-secrets ] || { echo "install: /home/lumen/bin/mask-secrets missing; lumen-run log would refuse to print" >&2; exit 1; }
cp -p "$BIN" "$BIN.bak-wq3606"
install -m 0755 "$HERE/lumen-run" "$BIN"
echo "--- verify ---"
python3 -m pytest -q "$HERE/test_lumen_run_log_mask.py"
echo "rollback:   install -m 0755 $BIN.bak-wq3606 $BIN"
