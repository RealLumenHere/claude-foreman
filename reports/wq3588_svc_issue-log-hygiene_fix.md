# wq#3588 — fix issue-log-hygiene.service (failed)

## Root cause

`issue-log-hygiene.service` runs
`ops/wq2817-issue-log-pointer-integrity/alarm_new_findings.py` against
`/home/lumen/kb/ISSUE-LOG.md` and exits 1 on any pointer-integrity finding not
already in the pinned baseline (`known-findings-baseline.json`, 131 known).
Two live findings were pushing the count to 132, both genuine data defects in
`kb/ISSUE-LOG.md` introduced by recent appends, not a bug in the probe:

1. **ONE-WAY row 1176 -> 1178** (`kb/ISSUE-LOG.md:1325`): row 1176's
   recurrence note ("see row 1178 for the a1 permission loop") pointed at row
   1178, but row 1178 (`kb/ISSUE-LOG.md:1327`) never cited row 1176 back —
   the exact signature `probe_pointers.py`'s ONE-WAY check exists to catch
   (a mis-typed or incomplete cross-reference).
2. **DANGLING row 1211 -> "row 2671172"** (`kb/ISSUE-LOG.md:1360`): row
   1211's solution text wrote "deepseek row 2671172", but `2671172` is an
   `aq` alert-queue row id, not an ISSUE-LOG.md row number — `CITE_RE`
   matches any bare `row N`, so it read this as a dangling cross-reference
   to a nonexistent ISSUE-LOG row.

## Fix

`kb/ISSUE-LOG.md` (live ops data file, separate repo from this worktree):

- Row 1178 (`kb/ISSUE-LOG.md:1327`): appended "Row 1176 is the bin-drift-rescue
  cleanup this permission loop blocked a1 on", giving row 1176 the back-reference
  the ONE-WAY check requires.
- Row 1211 (`kb/ISSUE-LOG.md:1360`): wrapped the alert-queue id in a code span —
  `` `row 2671172` `` with a "(aq alert id, not an ISSUE-LOG row)" annotation —
  so `_CODE_SPAN_RE` blanks it out of citation matching, per the same
  precedent documented in `probe_pointers.py:74-76` (quoted row numbers inside
  backticks are not pointers).

## Verification

`python3 ops/wq2817-issue-log-pointer-integrity/alarm_new_findings.py --path /home/lumen/kb/ISSUE-LOG.md`:

- Before: `live findings: 132`, `NEW ONE-WAY row 1176 ... -> 'see row 1178'`,
  `ALARM (1 new finding(s) not in baseline)`.
- After both edits: `live findings: 131`, `OK (no new findings beyond baseline)`
  — exact match with the pinned baseline, no new findings, no resolved-baseline
  drift.

`systemctl reset-failed issue-log-hygiene.service` / `systemctl start
issue-log-hygiene.service` are **not on this worker's Bash allowlist**
(`harness/worker-settings.json` only allows `systemctl status|is-active|
is-enabled|show|list-*|cat*` and one hardcoded `restart lumen-messenger`,
confirmed by direct refusal: `This command requires approval`). I could not
clear the unit's `failed` state or force an immediate re-run myself, so
`sentinel-watch --check svc/issue-log-hygiene.service` still reports `STILL
PRESENT: service issue-log-hygiene.service is failed` at the time of this
report, even though the underlying cause (the two live findings above) is
fixed and the probe now passes clean when run directly. The service's own
timer will clear the failed state on its next scheduled run (next hygiene
alarm run will exit 0 against the now-clean live file). Widening the
allowlist to include `Bash(systemctl reset-failed issue-log-hygiene.service)`
and `Bash(systemctl start issue-log-hygiene.service)` (or a generic
sentinel-svc-remediation wrapper) would let a future worker close this loop
without waiting on the timer.
