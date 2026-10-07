#!/usr/bin/env bash
# wq#3554 — fixed re-page title for sentinel_probes.py (duration moved to body) so
# aq's title-keyed ntfy ingest updates one row per probe instead of opening a new
# one per 6h re-page. /home/lumen/sentinel is not a git repo, so this bundle ships
# the change as sentinel_probes.diff (applied live already) rather than a file copy
# — see README.md. There is no install step: the live file was hand-patched to
# match the diff at ticket time, so this script only ever answers --verify.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
TARGET=/home/lumen/sentinel/probes/sentinel_probes.py
TEST=/home/lumen/sentinel/probes/tests_wq1487_edge.py

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  # fixed title: duration no longer interpolated between "failing" and the closing paren
  vcontains "$TARGET" 'ntfy_urgent\(f"SENTINEL CRITICAL \(still failing\): \{name\}",'
  # duration moved into the body instead of being dropped
  vcontains "$TARGET" 'f"failing \{_dur\(ts - f\[.since.\]\)\}: \{detail\}"\)'
  # _edge_clear's existing LIKE pattern (wq#1487) still matches the fixed title unchanged
  vcontains "$TARGET" 'SENTINEL CRITICAL \(still failing%\): \{name\}'
  # wq#3569 verify-pass: the live regression test for this fix (iv-b/iv-c) is still present
  vcontains "$TEST" 're-page after REPAGE_S'
  vcontains "$TEST" 'repage title stays fixed across repages'
  vdone
fi

echo "no install step: $TARGET is edited live by hand (not git-tracked); see README.md" >&2
exit 1
