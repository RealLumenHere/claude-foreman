#!/usr/bin/env bash
# wq#1302 — nothing to install: this bundle is an evaluation (README.md, inventory.json)
# plus a read-only audit script that runs from the repo. This script exists for the
# wq#1696 deploy-verify contract: --verify proves the audit's live inputs are reachable
# on this node and the script still parses. It writes nothing (no .pyc, no --json).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  [ -r "$SRC/cron_migration_audit.py" ] || _vmiss "cron_migration_audit.py unreadable"
  python3 -c 'import sys; f=sys.argv[1]; compile(open(f).read(), f, "exec")' \
    "$SRC/cron_migration_audit.py" 2>/dev/null || _vmiss "cron_migration_audit.py does not compile"
  systemctl list-timers --all --no-pager >/dev/null 2>&1 || _vmiss "systemctl list-timers not answering (audit's live input)"
  vdone
fi

echo "wq#1302: no install step — run cron_migration_audit.py from $SRC (see README.md)."
