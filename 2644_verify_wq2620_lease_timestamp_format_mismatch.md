# wq#2644 — verify-pass (archetype 8) of wq#2620 (PR #1008, `43d7e96f25`)

Verifier: claude-sonnet-5@high, fresh instance (same model as author, claude-sonnet-5).
Verdict: **CONTRADICTED** — the "unexpired lease" / "live slock" liveness criteria the
pruner and its own README claim to implement are dead code against the real `wq` database:
every real `lease_expires` and `slocks(_ro).expires_at` value silently fails to parse, so
an expired claim or expired slock is always treated as still-live. Safe direction (never
causes wrongful deletion — the opposite of the ticket's "do NOT touch" concern), but it
means a documented, tested-looking safety criterion never actually fires in production, and
a whole class of worktrees this ticket exists to reclaim (abandoned-but-still-`claimed`
tickets, stale slocks) is silently exempted from pruning forever.
Escalated as **wq#2669** (opus-5@high judgment, `deps=2620`).

## What wq#2620 shipped

`ops/wq2620-prune-stale-worktrees/prune_worktrees.py` (397 lines) + 13 tests + install.sh/
systemd unit + README/RUN-LOG, confirmed via `git show 43d7e96f25 --stat`. Real fleet sweep:
1032/1032 worktrees removed, ~8.85 GiB reclaimed, 0 failures.

## Confirmed correct

- **Discovery/registry/merge-base logic.** `discover_worktrees()` only looks directly under
  the two roots (matches ticket scope), `linked_worktree_repo()` correctly distinguishes a
  linked worktree (`.git` file with `gitdir:` pointing through `/.git/worktrees/`) from a
  primary checkout (`.git` directory) or stray dir. `is_merged()` uses
  `merge-base --is-ancestor branch origin/<default>`, not a commit count — correctly leaves
  a squash-merged branch alone rather than guessing it safe, exactly as the ticket demanded
  ("do NOT touch a worktree with unmerged commits").
- **Apply-mode size-before-removal fix.** `dir_size()` runs before `git worktree remove` in
  `run()` (prune_worktrees.py:340-343) — RUN-LOG.md documents this was a bug found and fixed
  mid-run (apply-mode originally measured after removal, always reporting 0 bytes);
  confirmed the shipped code has the fix.
- **Fail-closed on DB errors.** `_db_conn()` returning `None` (missing db) or a query raising
  both cause `wq_claim_is_live`/`slock_is_live` to return `(True, ...)` — verified by reading
  prune_worktrees.py:192-199, 231-239: correct, matches the "any read failure fails closed"
  claim.
- **`--apply` vs dry-run.** `run()` only calls `git worktree remove` when `apply=True`
  (prune_worktrees.py:344); dry-run never touches disk. Confirmed by code inspection.
- **fworker naming assumption.** `FWORKER_WT_RE = r"^wq(\d+)-a\d+$"` matches
  `make_worktree()`'s actual naming (`harness/fworker.py:2052`:
  `wt = os.path.join(WORKTREES_ROOT, f"wq{ticket_id}-a{attempt}")`) — every worktree under
  `/home/lumen/worktrees` is guaranteed to hit the wq-claim liveness branch, not the
  unguarded "no liveness record to check" fallback at prune_worktrees.py:283. Confirmed by
  reading fworker.py's worktree-creation path; this fallback is theoretically reachable only
  by a hand-made worktree under that root, which is out of the ticket's stated scope.

## Contradiction, with evidence

The pruner's own README (README.md:47-50) claims, as a tested behavior: *"a live wq claim or
slock blocks pruning, an **expired lease does not**"*. `prune_worktrees.py` parses stored
timestamps with `_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"` (prune_worktrees.py:54) in `_parse_ts()`
(:67-71), used on both `lease_expires` (`wq_claim_is_live`, :207-210) and `expires_at`
(`slock_is_live`, :245-246).

The real `wq` database does not store timestamps in that format. `bin/wq`'s own format
constant is `FMT = "%Y-%m-%d %H:%M:%S"` (bin/wq:145, no `T`/`Z`), used to write every
`lease_expires` and `slocks(_ro).expires_at` value. Confirmed live against the production db:

```
$ sqlite3 /home/lumen/agents/work-queue/queue.db \
    "SELECT id, status, lease_expires FROM tasks WHERE lease_expires IS NOT NULL LIMIT 5;"
1160|done|2026-09-02 13:08:30
2546|claimed|2026-09-25 09:54:00
2617|claimed|2026-09-25 10:20:06
...
$ sqlite3 /home/lumen/agents/work-queue/queue.db \
    "SELECT holder, expires_at FROM slocks LIMIT 3;"
fworker-wq1691|2026-09-26 14:53:47
fworker-wq2631|2026-09-25 13:28:55
```

`prune_worktrees.py`'s own `_parse_ts` on that exact shape:

```
>>> _parse_ts("2026-09-02 13:08:30")
None
```

`strptime` raises on the missing `T`/`Z`, `_parse_ts` catches it and returns `None`
(prune_worktrees.py:67-71) — silently, no log, no distinct reason string. Both call sites
treat `None` as "no evidence of expiry," not "expired":

```python
# wq_claim_is_live, :207-211
if lease_expires:
    exp = _parse_ts(lease_expires)
    if exp is not None and exp < _now_utc_naive():
        return False, f"claimed but lease expired {lease_expires}"
return True, f"status=claimed, lease={lease_expires or 'none'}"
```

Reproduced end-to-end against an isolated throwaway db (not the production db) with a claim
whose lease expired 1.5 hours ago, in the real `wq` format:

```
$ python3 repro_lease_bug.py
current UTC naive: 2026-09-25 09:28:29.668705
lease value in db: 2026-09-25 08:00:00 (in the past)
wq_claim_is_live(99999) -> (True, 'status=claimed, lease=2026-09-25 08:00:00')
```

Expected `(False, "claimed but lease expired 2026-09-25 08:00:00")`; got `(True, ...)` —
reported "live" for an already-expired claim. `slock_is_live` has the identical bug against
`slocks`/`slocks_ro.expires_at`, same `_TS_FMT`, same real-format mismatch, same
`exp is None` → "still live" fallthrough (prune_worktrees.py:245-246).

**Why the shipped tests didn't catch it:** `tests/test_prune_worktrees.py:127` builds its
"expired lease" fixture as `'2000-01-01T00:00:00Z'` — the ISO format the parser expects, not
the real `wq`-format `'2000-01-01 00:00:00'` the production db actually contains. The test
validates the comparison logic in isolation; it never exercises the parser against
real-shaped data, so it passes while the exact scenario it's meant to cover (an expired
claimed-ticket lease) is unreachable in production.

**Materiality:** this doesn't put any live work at risk — a parse failure fails toward "skip,
never prune," the safe direction the ticket asked for. But it defeats half of criterion (d)
as shipped and documented: a ticket left `status='claimed'` by a crashed/abandoned fworker
(with a lease that has long since expired, if `wq reap`'s independent sweep hasn't caught it
yet) or a stale slock row from a session that never called `wq retire`/`slock release`
permanently blocks pruning of that worktree — indefinitely, silently, with a `reason` string
(`"live wq claim (status=claimed, lease=...)"`) that reads as a legitimate skip, not a bug.
Given wq#2620 exists specifically because "no automatic or periodic cleanup exists for
landed-and-merged worktrees" and 2387 directories had already piled up, this is exactly the
kind of leak the ticket was filed to close, and it's currently only closed by luck (no
currently-`claimed` ticket in the live db has an expired lease right now — checked; likely
because `wq reap` clears those before they'd matter here) rather than by working code.

## Probe run

Re-ran the shipped probe on the real fleet, dry-run + no-fetch (matches the ticket's
"run once by hand first with --dry-run"):

```
$ python3 ops/wq2620-prune-stale-worktrees/prune_worktrees.py --no-fetch
dry-run: scanned=1454 prunable=21 skipped=1433 size=304M
```

No crash. 21 new prunable worktrees have accumulated since wq#2620's own post-apply dry run
(which found 5) — expected, normal ongoing churn from fworker attempts landing since then,
not a regression. The probe's headline
behavior (scan, classify, report) is intact; the defect is specifically in the lease/slock
*expiry* sub-check, which the current fleet state doesn't happen to exercise either way.

## Recommendation

Do not re-implement or patch this here (out of scope for a verify-pass; the fix is a
one-line format-string change plus a test fixture correction, but that's an implementation
call for whoever picks up the escalation). Escalating as a judgment ticket
(opus-5@high, `deps=2620`): confirm the intended fix (change `_TS_FMT` to
`"%Y-%m-%d %H:%M:%S"`, or normalize at read time) doesn't have a second-order effect on any
other caller of `_parse_ts`, and decide whether the shipped test fixtures should be corrected
to the real format so this class of bug can't hide behind a green test suite again.
