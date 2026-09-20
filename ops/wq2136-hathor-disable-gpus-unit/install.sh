#!/usr/bin/env bash
# wq#2136 bundle: hathor/nephthys disable-gpus.service, one-line ExecStart.
# No automated install: the unit is on HOLD (RUNBOOK.md) and research/ISIS_POWEROFF_RCA.md
# section 5 says to retire it. --verify reports the live state on each node, read-only.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
NODES="hathor nephthys"
DST=/etc/systemd/system/disable-gpus.service

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  for n in $NODES; do
    live="$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$n" "cat $DST" 2>/dev/null)" || {
      echo "ERR cannot read $DST on $n"; _V_ERR=1; continue
    }
    printf '%s\n' "$live" | cmp -s "$SRC/disable-gpus.service" - || _vmiss "differs: $n:$DST"
  done
  vdone
fi

echo "wq2136: no automated install (HOLD, see $SRC/RUNBOOK.md)" >&2
exit 3
