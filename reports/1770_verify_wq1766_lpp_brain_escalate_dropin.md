# wq#1770 — Adversarial verify-pass of wq#1766 (install LPP brain-escalate drop-in before PR #116 merges)

**Verdict: CONFIRM** — PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/472 (merged
`90046a9`, squash of `05fe3b4`+`71753aa`) does exactly what it claims: installs
`35-brain-escalate-1664.conf` into `/etc/systemd/system/lumen-pokemon-live.service.d/`,
restarts the live unit, and pre-empts the exit-2 boot gate that wq#1664/PR#116 will add to
`src/live_agent.ts`. No fabrication found.

## What was shipped

`90046a9`, 5 files, 147 insertions, 2 deletions:
- `ops/wq1766-lpp-brain-escalate-dropin/35-brain-escalate-1664.conf` — reference copy, new
- `ops/wq1766-lpp-brain-escalate-dropin/install.sh` — install/reload/restart/verify script, new
- `ops/wq1766-lpp-brain-escalate-dropin/RUNBOOK.md` — problem/fix/rollback doc, new
- `acceptance.json` / `evidence.json` — AC1 citation + `deploy_target`, edited

## Claims checked against live state (lumen-run detached jobs, no ssh needed —
worker already runs on lumen; `needs: ssh(anubis)` capability-lint tag unexercised, same as
wq#1763/wq#1765's reports — no `ssh` verb in this lane's allowlist)

- **Byte-identity of the shipped conf**: `md5sum` of `origin/fix/wq1664-lpp-escalate-and-actretry-must-not-be-th-a1:deploy/lumen-pokemon-live.service.d/35-brain-escalate-1664.conf`
  (the wq1664/PR#116 branch, fetched live) = `829ee6f4a6086acf60cdba129e892318`, exact match
  to both the RUNBOOK's claimed hash and the repo's reference copy. Not stale relative to
  wq1664's current tip.
- **Live file present, correct content**: `cat
  /etc/systemd/system/lumen-pokemon-live.service.d/35-brain-escalate-1664.conf` on the host
  returns the exact 30-line file (verbatim, comments included) — confirmed via a detached
  `lumen-run` job.
- **Effective environment wins**: `systemctl show lumen-pokemon-live -p Environment` shows
  `LPP_BRAIN_PRIMARY=glm-5.3-flash-zai`, `LPP_BRAIN_ESCALATE=cerebras-oss-120b`,
  `LPP_BRAIN_ACTRETRY=cerebras-oss-120b` — the `35-` drop-in is correctly winning over `30-`
  (lexical drop-in order), not just present-but-shadowed.
- **Service healthy since the actual restart**: `ActiveState=active`,
  `ActiveEnterTimestamp=Fri 2026-09-11 03:45:50 UTC`, `MainPID=3602400`;
  `journalctl -u lumen-pokemon-live -n 80 | grep '\[config\] FATAL'` → no match.
- **Gate logic re-derived from source, not assumed**: read
  `brainConfigErrors()` on the wq1664/PR#116 branch (`src/config.ts:338-352`) directly — it
  only requires `escalate != primary` and `actRetry != primary` independently (no check that
  escalate and actRetry differ from *each other*), so setting both to the same
  `cerebras-oss-120b` value, as this drop-in does, legitimately satisfies the gate. Confirmed
  this isn't a guess about untested code.
- **Ran the actual acceptance probe**, not a restatement of it: imported
  `harness/acceptance.py` and called `_ops_deploy_evidence()` directly against the live
  `deploy_target` and the worktree's reference conf → `True`, byte-match. Then ran the full
  `acceptance.verify()` against `acceptance.json`/`evidence.json` with `base_ref` set to
  `05fe3b4^` (the actual pre-merge base, since this worktree's `HEAD` already contains the
  merge and diffing against `origin/main` as the default `base_ref` produces a false-negative
  empty diff) → `{'ok': True, 'failed': []}`. AC1's citation
  (`install.sh:16-24`) also independently checked out: those lines are exactly
  `install`/`daemon-reload`/`restart`/`sleep`/`is-active`/FATAL-grep, matching the claim text.
- **No competing automation**: `ls` of the drop-in directory shows only the expected files
  plus historical `30-brain-env.conf.bak.*` snapshots; `systemctl list-timers` has no timer
  touching `lumen-pokemon-live`; grep of the foreman tree for `30-brain-env` outside `_runs/`
  snapshots turns up only research docs, no script that would regenerate/overwrite the `30-`
  file or delete the `35-` drop-in.

## Discrepancies found — none material

None. The RUNBOOK's claimed md5, timestamps, and unit name are all exact matches to live
state.

## Gaps NOT closed by this PR (disclosed, not misrepresented)

- This ticket verifies wq#1766's own claim (drop-in installed, unit healthy) — it does not
  re-verify wq#1664/PR#116's `brainConfigErrors()` correctness itself; that's wq#1768's
  separate verify-pass. I did read the gate logic (above) far enough to confirm this specific
  fix satisfies it, but did not audit wq#1664 end-to-end.
- If PR#116 lands with a *different* final value than `ffd150b`'s current
  `deploy/.../35-brain-escalate-1664.conf` (e.g. a review comment changes the model choice
  before merge), this pre-installed drop-in would go stale relative to that new value, though
  it would still satisfy the boot gate (any value != primary passes). Not checked here since
  PR#116 was still open at verification time — flagged as a residual risk, not a defect in
  wq#1766's diff.

## Disposition

No contradiction, no escalation to opus-5@high. wq#1766's PR is an honest, correctly-scoped
ops fix: the drop-in it claims to have installed is live, byte-exact, winning in the merged
environment, the unit is healthy with no FATAL, and both the acceptance probe and the
underlying gate logic independently confirm the fix is real and correct.
