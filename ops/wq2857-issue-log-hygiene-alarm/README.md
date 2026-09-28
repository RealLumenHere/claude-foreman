# issue-log-hygiene-alarm (wq#2857)

wq#2817 built `ops/wq2817-issue-log-pointer-integrity/probe_pointers.py`, which reports
every `DUP`/`DANGLING`/`ONE-WAY` cross-row-pointer defect in `kb/ISSUE-LOG.md`. Run against
the live file it currently reports a 128-finding backlog (122+ pre-existing duplicate row
numbers plus new ones that landed after wq#2817's own baseline of 124 at rev `10e77b2` —
row 869 from a same-day collision between wq#2787 and wq#2836, row 885 from a later one).
Wiring `probe_pointers.py` itself into a periodic check would therefore either fail on
every single run forever, or need the baseline widened by hand each time — neither
catches a *new* collision, which is the actual problem (doctrine makes every worker read
this file, so an ambiguous "row N" sends readers to the wrong entry — wq#2763's finding).

`alarm_new_findings.py` (same directory as the checker) instead diffs the live finding set
against `known-findings-baseline.json`, a pinned snapshot of the findings already known and
deliberately left as backlog (see `reports/2857_policy_issue_log_row_disambiguation.md` for
why they aren't renumbered in place). It alarms — nonzero exit, `ALARM (...)` on stdout —
only on a finding that is *not* in that snapshot: a fresh collision, caught within one
30-minute timer run instead of surfacing two days later in a downstream ticket.

## Install

Root-only, via `lumen-run` (this repo's convention for privileged unit installs):
```
lumen-run start --root -- bash /home/lumen/foreman/ops/wq2857-issue-log-hygiene-alarm/install.sh
```
Installs `issue-log-hygiene.timer`/`.service`, enabled and started immediately. Manual run:
`systemctl start issue-log-hygiene.service`. Logs: `journalctl -u issue-log-hygiene.service`.
Last summary line: `/home/lumen/logs/.issue-log-hygiene.laststate` (same convention
`session-handoff`'s `DETECTORS` list reads for the other Day-0 detectors — add
`("issue-log-hygiene", ".issue-log-hygiene.laststate")` there to surface it in
`HANDOFF.md` too).

## Rebaselining

The baseline only shrinks or grows on a deliberate, reviewed step — never silently as a
side effect of a periodic run (`alarm_new_findings.py` never writes it). Two cases:

- **A finding was actually fixed** (a duplicate renumbered, a pointer corrected): re-run
  the capture below. `RESOLVED` lines in the alarm's output name the finding key that
  dropped out; confirm each one before recapturing.
- **A new collision landed and was triaged as "leave it, like the rest of the backlog"**
  rather than fixed on the spot: same recapture, after recording the triage decision
  (e.g. a note in the ISSUE-LOG row itself, or a follow-up ticket) — don't fold it in
  silently just to quiet the alarm.

```
python3 -c "
import json, importlib.util
spec = importlib.util.spec_from_file_location('probe_pointers',
    '/home/lumen/foreman/ops/wq2817-issue-log-pointer-integrity/probe_pointers.py')
probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)
found = probe.rows(probe.load(None, '/home/lumen/kb/ISSUE-LOG.md'))
keys = sorted(probe.finding_keys(found))
json.dump({'captured': '<UTC timestamp>', 'captured_by': '<ticket>',
           'count': len(keys), 'keys': keys},
          open('/home/lumen/foreman/ops/wq2817-issue-log-pointer-integrity/known-findings-baseline.json', 'w'),
          indent=2)
"
```

## Tests

`harness/tests/test_wq2857_issue_log_alarm.py` — fixture-based, does not read the live kb
file or depend on the committed baseline's exact contents (beyond a well-formedness check).
