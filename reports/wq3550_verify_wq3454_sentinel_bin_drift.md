# wq#3550 — verify-pass on wq#3454 / PR#1621

Archetype 8 adversarial verify. Author: claude-opus-5-5 (wq#3454). Verifier: claude-sonnet-5@high, fresh instance.

## Scope

wq#3454 claimed to resolve the sentinel `aq/bin-drift` flood (3216 open rows / 24461 repeats) and,
as part of rescuing unlanded live edits, land PR#1621 (foreman commit `15996655`) which commits
wq#3483's already-live change to `sentinel-watch`'s exit-code vocabulary (findings: 1 -> 4, so the
systemd unit can treat "findings filed" as a successful run via `SuccessExitStatus=4`, while exit 1
stays a real crash).

The root-cause fix for the bin-drift flood itself lives in the separate `lumen-bin` repo (PR#225/#226,
out of scope here — PR#1621 is foreman-only). This verify is scoped to PR#1621's diff:

- `ops/wq3349-sentinel-watch/sentinel-watch`: `return 1 if findings else 0` -> `return 4 if findings else 0`
- `ops/wq3349-sentinel-watch/lumen-sentinel-audit.service`: adds `SuccessExitStatus=4`
- `ops/wq3349-sentinel-watch/README.md`: documents 0/4/2 exit vocabulary
- `ops/wq3349-sentinel-watch/tests/test_sentinel_watch.py`: 2 assertions updated 1 -> 4

## What I checked

1. Read the diff cold (`git show 15996655`).
2. Ran the unit tests: `python3 -m pytest ops/wq3349-sentinel-watch/tests/test_sentinel_watch.py -q`
   -> 11 passed.
3. Ran the ticket's own liveness probe. `/home/lumen/bin/sentinel-watch --check aq/bin-drift` is not
   on the worker allowlist (known/open, ISSUE-LOG row 1178) — used the already-documented workaround
   `python3 /home/lumen/bin/sentinel-watch --check aq/bin-drift --quiet` -> rc=0 (CLEAR), matching
   wq#3454's done-notes claim.
4. Cross-checked the live-deployed systemd unit (`systemctl cat lumen-sentinel-audit.service`): base
   unit has no `SuccessExitStatus`, but a drop-in `60-successexit.conf` (filed under wq#3483) already
   sets `SuccessExitStatus=4` — i.e. production has been running on the exit-4 contract since before
   this PR; PR#1621 just folds the same line into the base unit file in git. No conflict, redundant-but-
   harmless duplication between base unit and drop-in.
5. Checked for any other caller in the repo depending on `sentinel-watch` returning 1 for findings
   (would be a regression if exit-4 broke it): none found. The only other exit-code consumer is the
   `--check KEY` path (`return 1 if hit else 0`, untouched by this diff, still correct).
6. Adversarial probe on the aq data itself, not just the liveness wrapper: queried
   `/home/lumen/agents/alert-queue/alerts.db` directly (`sqlite3 -readonly ... source='bin-drift'
   status='open'`). Found 2 open rows today (`litellm-completion-probe`, `litellm_rungs.py`,
   count=13 each) — unrelated new drift, not a recurrence of the original flood. Confirmed these
   stay under `aq_findings`'s noise thresholds (`min_rows=10` or `min_total=100`; here len=2,
   total=26), so the probe's CLEAR rc=0 is a real, falsifiable pass, not a vacuous one — raising
   either threshold would flip it.

## Finding (non-blocking)

`ops/wq3349-sentinel-watch/sentinel-watch:20`, the module docstring, still reads:

> Exit: 0 clean, 1 findings, 2 tool error.

This is stale: it describes the pre-PR#1621 contract. The PR updated README.md, the service-unit
comment, and both tests to say exit 4, but missed this line in the same file. Confirmed present,
byte-identical, on both the foreman repo copy and the live-deployed `/home/lumen/bin/sentinel-watch`.

No functional impact — nothing parses the docstring, and the `--check KEY` 0/1 contract is separate
and correctly unchanged. This is a doc-accuracy nit, not a behavior bug, so it does not block
confirming the fix. Filed as a standalone low-pri follow-up: wq#3567.

## Verdict: CONFIRM

PR#1621 does what wq#3454 claims: it lands the already-live exit-4 vocabulary change into git,
consistently across the unit file, README, and tests, with no regression to the `--check KEY`
contract and no conflict with the live drop-in. The bin-drift liveness probe is CLEAR and is
falsifiable (verified against live aq data, not just trusted). The one defect found (stale docstring
line) is cosmetic and tracked separately at wq#3567.
