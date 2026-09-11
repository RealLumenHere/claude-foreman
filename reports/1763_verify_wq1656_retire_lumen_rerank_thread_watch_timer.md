# wq#1763 — Adversarial verify-pass of wq#1656 (retire lumen-rerank-thread-watch timer, 48h watch, 27 days old)

**Verdict: CONFIRM** — PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/464 (merged
`372d19d`) does exactly what it claims: documents the retirement of
`lumen-rerank-thread-watch.{timer,service}` in `docs/fleet/retired_services.yaml` and an
ops runbook, correctly declines to run the disallowed `systemctl stop/disable/mask` +
unit-file deletion from this build lane (house deny-list), and files a scoped follow-up
(wq#1762) with the exact steps a systemctl-mutate lane needs. No fabrication found.

## What was shipped

`372d19d`, 2 files, 114 insertions, 0 deletions, both docs:
- `docs/fleet/retired_services.yaml` — new `lumen-rerank-thread-watch` entry (45 lines)
- `ops/wq1656-retire-lumen-rerank-thread-watch/RUNBOOK.md` — new file (69 lines)

No script, unit, or systemd-state changes — matches the ticket's own admission that live
removal is out of scope for this lane.

## Claims checked against live state (this box, no ssh needed — worker already runs on lumen)

- `systemctl show -p LoadState,ActiveState,UnitFileState` for both units: timer =
  `loaded/active/enabled`, service = `loaded/inactive/static`. Exact match to the PR's
  claimed sweep table.
- `/home/lumen/bin/rerank-thread-watch.sh:57` — confirmed verbatim: `[ -f "$DONE" ] &&
  exit 0`. `DONE=/home/lumen/minerva/v4/reports/596_watch_ROLLED_BACK` (line 22).
- `journalctl -u lumen-rerank-thread-watch.service --since -30min`: bare
  `Starting.../Deactivated successfully./Finished...` every 5 min, zero `TRIP`/rollback/ntfy
  lines. Matches the claimed no-op pattern.
- Stronger indirect proof the DONE marker exists (couldn't stat the file directly — see
  Gaps): the script's own window-expiry branch (lines 59-68) *self-disables* the timer
  (`systemctl disable --now`) once `BASE_AGE >= 604800` (7d) with no trip. 27+ days have
  elapsed since the 2026-08-14 install, so if `$DONE` did not already exist, this branch
  would have fired days ago and the timer would already read `disabled`. It still reads
  `enabled`/`active`, which is only possible if execution is short-circuiting at line 57
  every run — i.e., `$DONE` is present, exactly as claimed.
- New YAML entry parses cleanly in isolation and matches the established schema (`name:
  units: load_state: retired_on: reason: ticket: data: guards:`), same shape as the
  `agent5-split`/`lumen-agent` entries already in the file.
- wq#1762 (follow-up, `ops` lane, open) carries the correct scope: disable+remove both
  units, `daemon-reload`, confirm "could not be found", `unit-drift snapshot` — matches
  RUNBOOK.md's Apply section exactly, not a vague restatement.

## Discrepancies found — neither changes the verdict

1. **"48h" framing is stale.** `rerank-thread-watch.sh:61-62` documents "Frank decision C
   2026-08-14: 7d window (was 48h)" — the actual window was widened to 7 days the same day
   it was installed, but the PR's yaml/runbook text (and wq#1656's own title) still call it
   a "48h auto-expiring" watch throughout. Immaterial to the retirement conclusion: the
   DONE-marker check (line 57) executes *unconditionally before* the window-expiry check
   (lines 59-68), so the no-op verdict holds regardless of which window length applies —
   the watch isn't a no-op because a window expired, it's a no-op because it already
   tripped and rolled back once, long ago.
2. **"27 days" is off by about one.** 2026-08-14 to 2026-09-11 (this PR's merge date) is 28
   days, not 27. Inherited from wq#1627's atlas sweep date (a day or two earlier); doesn't
   affect the "weeks past any reasonable window" conclusion either way.
3. **Pre-existing YAML break, not introduced here.** `docs/fleet/retired_services.yaml` as
   a whole fails `yaml.safe_load` (`mapping values are not allowed here`, line 57 of the
   file — an unrelated wq#1112/wq#1643 `guards:` block with an unescaped `: ` inside a
   plain scalar). Confirmed via `git show 372d19d~1:...` that this break predates wq#1656's
   commit. The file's only in-repo consumer
   (`ops/wq1609-minerva-contradiction-scan/lumen_contradiction_scan.py`) opens it as plain
   text, not through a YAML parser, so this is latent and not this PR's regression.

## Gaps NOT closed by this PR (disclosed, not misrepresented)

- Live unit removal itself — explicitly deferred to wq#1762, correctly, because this lane
  has no systemctl-mutate grant (deny-list blocks `stop`/`disable`/`mask`). Not a gap in
  the PR's own claim, since it never claimed to remove the units.
- Could not directly `stat`/`ls` the `/home/lumen/minerva/v4/reports/596_watch*` files
  named in the yaml's `data:` block — that path is outside this worktree's granted dirs.
  Substituted the indirect self-disable-timing argument above, which is conclusive without
  needing direct file access.
- Ticket's `needs: ssh(anubis)` capability-lint tag: not exercised — turned out unnecessary
  since all live-state checks (systemctl, journalctl) run locally on this box already; no
  `ssh` verb is even in this lane's allowlist. Same structural note as wq#1556's report.

## Disposition

No contradiction, no escalation to opus-5@high. The PR is an honest, correctly-scoped
documentation-only retirement that respects this lane's deny-list, backed by live evidence
that matches its claims exactly plus one independent corroborating signal (the timer's own
self-disable logic never fired despite the window having long expired, which requires the
DONE marker to already exist).
