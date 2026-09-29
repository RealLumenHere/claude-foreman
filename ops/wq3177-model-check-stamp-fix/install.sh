#!/usr/bin/env bash
# wq#3177 (a) — session-handoff shows "model-check: last run never ran (no
# stamp)" even though logs/model-check.log keeps updating. Root cause:
# lumen-model-check runs from a plain crontab entry (`*/15`, see
# bin/fleet-status:271), not a systemd unit, so the usual
# `ExecStartPost=-lumen-cron-stamp %n` wiring never applies — nothing ever
# wrote `/home/lumen/logs/.stamp.model-check`, which is the file
# bin/session-handoff:135 unconditionally reads for every detector.
#
# Fix: patch lumen-model-check itself to call lumen-cron-stamp directly (a
# trap on EXIT, so it fires on success, gateway-down, and invalid-model exits
# alike) — this is invoker-agnostic, so it works under cron exactly as it
# would under systemd. Run as root (cron scripts under /home/lumen/bin are in
# the primary tree, same convention as wq#2910/wq#1658):
#   lumen-run start --root --name wq3177-install -- \
#     bash /home/lumen/foreman/ops/wq3177-model-check-stamp-fix/install.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DST=/home/lumen/bin/lumen-model-check
ATOMIC_INSTALL="$SRC/../wq2856-atomic-install/install-script"

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/lumen-model-check" "$DST"
  vdone
fi

echo "== installing lumen-model-check (adds lumen-cron-stamp trap, wq#3177) =="
"$ATOMIC_INSTALL" -m 0755 "$SRC/lumen-model-check" "$DST"

echo "verify:   bash $SRC/install.sh --verify   # exit 0 = live"
echo "smoke:    $DST >/dev/null; ls -la /home/lumen/logs/.stamp.model-check"
echo "rollback: git -C /home/lumen/bin checkout -- lumen-model-check"
