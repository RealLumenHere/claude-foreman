# wq#2620 — prune stale worktrees

`lumen-pr commit`/`land` never removes the linked worktree it worked in (wq#2561's
own worktree, `wq2561-a1`, is still on disk after landing), and fworker's per-ticket
worktrees under `/home/lumen/worktrees` have no cleanup step of their own. 2387
directories had accumulated across `/home/lumen/scratch/worktrees` and
`/home/lumen/worktrees` as of 2026-09-25, 718M+ in scratch alone. `prune_worktrees.py`
is the safe pruner; `lumen-prune-worktrees.timer` is what keeps it running.

```
python3 prune_worktrees.py                    # dry run, human summary
python3 prune_worktrees.py --json             # dry run, full JSON
python3 prune_worktrees.py --apply            # actually `git worktree remove`
python3 prune_worktrees.py --no-fetch         # skip `git fetch --prune`, use local refs
python3 prune_worktrees.py --grace-days N     # wait N days after landing before pruning (default 7)
```

A worktree is only pruned if **all** of:

1. **Merged** — `git merge-base --is-ancestor <branch> origin/<default>`. This is a
   reachability check, not a commit count, so a squash-merged branch (content
   landed, exact commits not reachable) is reported unmerged and left alone —
   conservative in the direction the ticket asks for.
2. **Clean** — `git status --porcelain` is empty. Untracked files count; the ticket
   says "uncommitted changes", not "uncommitted tracked changes".
3. **Registered and unlocked** — present in `git worktree list --porcelain` (the
   authoritative source; the directory listing alone never confirms prunability),
   not `locked`, not `detached`.
4. **Past the grace period** — it **landed** at least `--grace-days` ago
   (default **7**, wq#2909). A branch that merged moments ago is left alone even
   though it already passes checks 1-3, so a same-day revert still finds its
   worktree intact.

   The landing time is the **latest** timestamp any source can evidence, which
   is the smallest age and so the most protection (`landing_age_days`):
   - the **landing commit**'s date — the oldest commit that both descends from
     the branch tip and is reachable from `origin/<default>`
     (`git rev-list --ancestry-path <branch>..origin/<default>`, last line). For
     a `--no-ff` merge that is the merge commit; for a fast-forward, the next
     commit pushed onto the default branch.
   - **`tasks.done_at`** for a `wqNNNN-aN` worktree — when its ticket actually
     closed. This is the only source covering the case git cannot: a
     fast-forward whose branch tip is *still* the default branch's tip leaves no
     commit dating the landing.
   - the **branch tip**'s own commit date — a floor, not the answer. It is what
     remains for a branch that never diverged, where no landing event exists to
     date.

   wq#2909 originally used the branch tip alone. A commit date is only a *lower*
   bound on landing time, so a branch committed ten days before it merged read
   as ten days old and was prunable the instant the merge landed — zero revert
   window, the same bug wq#2909 was filed against. wq#2934 reproduced it,
   wq#2942 fixed it. Residual gap, by construction: a fast-forward still at the
   default branch's tip, in a *non*-fworker worktree (no `done_at`), falls back
   to the tip date. Git records nothing else there.

   No readable landing date at all fails closed — skipped, not guessed old
   enough.
5. **No live claim on it** —
   - `/home/lumen/worktrees/wqNNNN-aN`: ticket `NNNN` in the wq db
     (`/home/lumen/agents/work-queue/queue.db`) is not `status='claimed'` with an
     unexpired lease.
   - `/home/lumen/scratch/worktrees/<name>`: the `lumen-pr` owner record at
     `.owners/<name>` names an agent with no unexpired row in `slocks` or
     `slocks_ro`. No owner record at all (predates the ownership guard) means no
     claim to check — it still has to clear checks 1-3.

Any read failure on the liveness check (db missing, unparseable) or the landing-date
check **fails closed**: treated as live (or too new), never pruned. An unreadable
answer is not evidence of a safe one. An unreadable wq db makes `done_at` contribute
nothing to the landing clock rather than failing closed there — the liveness check
already treats that same condition as a live claim, so no prune can get past it.

Lease and slock expiry times are read with `_DB_TS_FMT` (`%Y-%m-%d %H:%M:%S`), the
format `bin/wq:145` and `bin/slock:43` actually write. Keep it separate from
`_LOG_TS_FMT`, which is only for the run log below — wq#2644 found the two had been
collapsed into one ISO-8601 constant, so every stored timestamp failed to parse and
nothing ever read as expired. `probe_wq2669_writer_format.py` checks the pruner still
agrees with the writers at a pinned revision.

Every run appends one JSON line to `/home/lumen/kb/logs/wq2620-prune-worktrees.jsonl`
(`--log` to override), dry run or apply, so there is always a paper trail of what
would have/did happen.

## Tests

`python3 -m pytest tests -q` — 26 tests, each against a real throwaway git repo (no
git mocking): merged+clean prunes, unmerged/dirty/locked all skip, a stray directory
with no `.git` file is ignored, a primary checkout is never a candidate, a live wq
claim or slock blocks pruning, an expired lease or slock does not, an unparseable one
skips and says so, a missing wq db fails closed, and `--apply` actually removes while
dry-run never touches disk. Every timestamp fixture is written in wq's stored format,
spelled out in the test file rather than imported from the pruner, so a fixture cannot
drift along with the constant it is supposed to check.

wq#2909 added the grace-period tests; wq#2942 rebuilt them on real git dates after
wq#2934 found the suite never exercised a branch whose commit date and landing date
diverge (the "past grace period" case reached 8 days by monkeypatching the age
function, so no git ancestry ran at all). The 26th test count now covers: a branch
committed 10 days ago but landed just now keeps its full window *and* says
`landing commit` in the reason (the wq#2934 regression); the same divergence on a
non-fworker scratch worktree, where no `done_at` exists, so the landing-commit clock
has to carry it alone; a fast-forward still at main's tip falling back to `done_at`,
and the same fast-forward pruning when that `done_at` is 10 days old (without which a
fix that simply never prunes would pass); `done_at` beating an older landing commit on
a reopened ticket; and a landing date no source can read failing closed.

`reports/wq2934_probe_grace_period_commit_vs_merge_age.py` is the standalone
reproducer, kept as a regression check: it exits 1 against the tip-commit clock and 0
against the landing clock.

## Production dry run, 2026-09-25

```
$ python3 prune_worktrees.py --json
```

See `RUN-LOG.md` in this directory for the actual counts and per-repo breakdown from
the real fleet — reclaimed size, prunable vs skipped, and why each skip category was
skipped (live claim, unmerged, dirty, locked). That file is evidence, not narration:
every number in it is the tool's own JSON output from the run named in it.

## What a worker can and cannot do

Discovery, classification and `--apply` all run as an ordinary `lumen` user process
— `git worktree remove` needs no privilege, and `os.listdir`/`open()` on a path
outside this session's declared `--add-dir` list are unaffected by that restriction
(it bounds the *shell* argument surface of specific commands like `ls`/`find`/`cd`,
not what a spawned process — `python3 prune_worktrees.py`, `git -C <repo>` from
inside it — goes on to open; wq#2428/wq#2456 hit and documented the same thing for
`timer_spend_audit.py`). So a BUILD worker can and did run the real sweep, dry-run
and apply both, without any root step.

What still needs root, same as wq#2428:

1. Installing/enabling `lumen-prune-worktrees.timer` — `/etc/systemd/system` +
   `systemctl enable --now`, both off the worker allowlist.

Run `bash install.sh` as `lumen` on Lumen with `sudo` available to wire the timer.

## Deploy checklist

1. `bash install.sh` — re-runs a dry-run sweep, then installs and enables the timer
   (daily, `--apply`, logs to the JSONL path above). Idempotent.
2. `systemctl list-timers lumen-prune-worktrees.timer` to confirm it's armed.
