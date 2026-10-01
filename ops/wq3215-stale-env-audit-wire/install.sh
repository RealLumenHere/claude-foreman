#!/usr/bin/env bash
# wq#3215/wq#3245 — install the reconciled stale-env-audit to /home/lumen/bin.
#
# This bundle owns the BINARY ONLY. The hourly unit and timer belong to
# ops/wq3215-stale-env-audit-timer, which is already installed; this bundle used to ship a
# second copy of the same two /etc/systemd/system paths with a different ExecStart, so
# whichever bundle installed last would have made the other's --verify fail forever
# (wq#3245). Docker coverage is turned on in the timer bundle's unit, not here.
#
# Run as lumen on Lumen (/home/lumen/bin is lumen-owned; no sudo needed):
#   bash /home/lumen/foreman/ops/wq3215-stale-env-audit-wire/install.sh
# From a sandboxed worker (bare `bash <abs path>` is not on the worker allowlist and
# Write into /home/lumen/bin is blocked by guard.sh — ISSUE-LOG #770/#1106):
#   /home/lumen/bin/lumen-run start -- bash /home/lumen/foreman/ops/wq3215-stale-env-audit-wire/install.sh
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# wq#1696 deploy-verify contract. Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vfile "$HERE/stale-env-audit" /home/lumen/bin/stale-env-audit
  vflag /home/lumen/bin/stale-env-audit --fail-on-drift
  vflag /home/lumen/bin/stale-env-audit --docker
  vflag /home/lumen/bin/stale-env-audit --aq
  vdone
fi

python3 -m py_compile "$HERE/stale-env-audit"
python3 -m pytest -q "$HERE/test_stale_env_audit.py"

# -o/-g only when root: a root-owned file here would leave the lumen-bin git repo holding a
# path lumen can no longer rewrite.
if [ "$(id -u)" = 0 ]; then
  install -o lumen -g lumen -m 0755 "$HERE/stale-env-audit" /home/lumen/bin/stale-env-audit
else
  install -m 0755 "$HERE/stale-env-audit" /home/lumen/bin/stale-env-audit
fi

# Post-install gate: the wq#3228 repro must no longer answer "clean" for a name the audit
# never examined. On the d35e196 build this exact argv printed "19 clean, 0 with drift" and
# exited 0. An install that cannot fail this line has not fixed anything.
set +e
/home/lumen/bin/stale-env-audit --fail-on-drift wq3245-nonexistent-unit.service --vars NOPE_VAR
rc=$?
set -e
[ "$rc" = 2 ] || { echo "FAIL: installed binary exited $rc for an unexamined unit, expected 2"; exit 1; }

echo "---"
echo "installed /home/lumen/bin/stale-env-audit"
echo "NOTE: that path is also tracked by the separate lumen-bin repo (d35e196). This bundle"
echo "      is now its source of truth; commit or discard the resulting diff there."
echo "The hourly timer lives in ops/wq3215-stale-env-audit-timer — install that bundle too"
echo "to pick up --docker in the scheduled run."
