#!/usr/bin/env bash
# wq#4701 — retire lumen-watchdog.{timer,service} + /home/lumen/agent/lumen_watchdog.py.
#
# The only install step is root: `sudo bash remove-units.sh` (this directory).
# This file answers the wq#1696 deploy-verify contract (ops/_verify.sh): read-only,
# exit 0 only once the units are gone from the host and the script is moved aside.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  for u in lumen-watchdog.timer lumen-watchdog.service; do
    ls="$(systemctl show "$u" -p LoadState --value 2>/dev/null || true)"
    [ "$ls" = not-found ] || _vmiss "unit still present: $u (LoadState=${ls:-unknown})"
  done
  [ ! -e /home/lumen/agent/lumen_watchdog.py ] \
    || _vmiss "script still in place: /home/lumen/agent/lumen_watchdog.py"
  vcontains /home/lumen/agent/lumen_watchdog.RETIRED.txt 'wq#4701'
  vcontains /home/lumen/fleet-units/known-removed.txt '^lumen-watchdog\.timer'
  vdone
fi

cat <<EOF
No unprivileged install step. Run as root:
  sudo bash $SRC/remove-units.sh
Then: bash $0 --verify
EOF
