#!/usr/bin/env bash
# wq#3756/wq#3764 — deploy-verify handler for the one-time archive-triage job.
#
# There is nothing to install: triage.py lives only in this repo and the artifact
# (REPORT.md) is already checked in. "Live" for a one-time job means the tool
# still runs read-only against minerva.db and still produces the stats line the
# report depends on — not that its counts stay frozen as the archive grows.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  if [ ! -r "$SRC/triage.py" ]; then
    _vmiss "absent: $SRC/triage.py"
  elif [ ! -r "$SRC/REPORT.md" ]; then
    _vmiss "absent: $SRC/REPORT.md"
  else
    TMP="$(mktemp -d)"
    cp "$SRC/triage.py" "$TMP/triage.py"
    if OUT="$(python3 -I "$TMP/triage.py" 2>&1)"; then
      case "$OUT" in
        *'"top_emitted"'*) ;;
        *) _vmiss "triage.py ran but did not emit the expected stats JSON: $OUT" ;;
      esac
    else
      _vmiss "triage.py failed against live minerva.db: $OUT"
    fi
    rm -rf "$TMP"
  fi
  vdone
fi

echo "wq3756-archive-triage is a one-time analysis job; nothing to install." >&2
exit 0
