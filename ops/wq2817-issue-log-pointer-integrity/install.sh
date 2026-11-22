#!/usr/bin/env bash
# wq#2817 issue-log-pointer-integrity: no separate deploy step. probe_pointers.py
# and alarm_new_findings.py are invoked IN PLACE from this primary tree --
# ops/wq2857-issue-log-hygiene-alarm's issue-log-hygiene.service ExecStart names
# this bundle's alarm_new_findings.py directly, so there is no copied artifact to
# byte-match (see that bundle's install.sh comment). A --verify that only checked
# "does the repo still contain probe_pointers.py" would be exactly the repo-only
# check ops/VERIFY_CONTRACT.md (wq#1696) says does not count: it cannot fail from
# drift that happens on the box. This one instead confirms the two facts that
# actually determine whether this bundle's code runs live: the deployed timer's
# unit still points at this bundle's script, and the timer that calls it on a
# schedule is active.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vcontains /etc/systemd/system/issue-log-hygiene.service \
    "wq2817-issue-log-pointer-integrity/alarm_new_findings\.py"
  vactive issue-log-hygiene.timer
  # wq#4910: no vtimer_scheduled for issue-log-hygiene.timer: intended-off, disabled by the
  # 2026-10-08 subtraction (scratch/subtraction/timers-disable-list.txt). Re-enabling it
  # is a decision, not a heal; whoever makes it adds vtimer_scheduled here.
  vdone
fi

echo "wq2817-issue-log-pointer-integrity has no install step of its own:" >&2
echo "probe_pointers.py and alarm_new_findings.py run in place from this primary" >&2
echo "tree, invoked by ops/wq2857-issue-log-hygiene-alarm's timer. Install that" >&2
echo "bundle to make this one live:" >&2
echo "  lumen-run start --root -- bash $(dirname "$SRC")/wq2857-issue-log-hygiene-alarm/install.sh" >&2
exit 0
