# wq#4467 verify-pass: wq#4463 `wq release --dead-worker` on a BLOCKED ticket flips it to open

**Target:** lumen-bin PR #264, merged `b5e75b1` (fix, in `/home/lumen/bin/wq`); regression
test + keeper landed in claude-foreman PR #2342, merged `23554c8a` (present as `112098fb`
on main, `23554c8a` on this verify branch — cherry-pick, same diff).
**Author model:** claude-sonnet-5. **Verifier:** claude-sonnet-5@high (this session, fresh
instance).
**Verdict: CONFIRM.**

## What the diff claims

Observed 2026-10-21 on ticket 2842 (blocked, block_reason 1359 chars): `wq release 2842
--dead-worker` left it `status=open, block_reason=NULL`. Root cause: the wq#1498/wq#1539
dead-worker-override branch in `_finish` only cleared the dead holder's stale claim, then
fell through into the generic `new_status == 'open'` UPDATE, which unconditionally sets
`status='open'` and wipes `block_reason`/`blocked_at`. At `open` the ~20min foreman
dispatcher can redispatch a ticket someone deliberately blocked (often Frank's own call)
within one cycle. 26 blocked tickets carried a dead `fworker` `claimed_by` at the time,
so the footgun was live.

Claimed fix (`wq:2581-2598` in the override branch, before `owner_ok` is reached): when
the ticket's current status is `'blocked'`, clear `claimed_by`/lease/hold and release
subsystem locks directly, `COMMIT`, and `return 0` before the generic UPDATE ever runs.
`status` and `block_reason`/`blocked_at` are left untouched; a non-blocked holder
(open/claimed) falls through to the pre-existing generic release path unchanged. Ships
with a keeper (`ops/wq4463-release-dead-worker-blocked/ensure_wq4463_release_dead_worker_blocked.py`)
and a regression test (`harness/tests/test_wq4463_release_dead_worker_blocked.py`, cases
A–D). Notes claim 15/15 dead-worker tests pass against the live `wq`, and that the
insertion required updating the wq#1498/wq#1539 keepers' own anchor strings (since the new
code lands inside their anchored text) — both re-verified `patched: true`.

## What I checked

- Read the diff cold: `git -C /home/lumen/bin show b5e75b1` (23 lines added to `wq`,
  nothing else touched) and `git show 23554c8a` in this worktree (test + two keeper
  updates + new keeper, 476 lines, test-only). Confirmed the fix lives in `wq:2581-2598`
  inside `_finish`, strictly before the `elif not owner_ok(...)` that governs the
  non-override path — so it never reaches ordinary owner-eligibility logic, by design
  (mirrors how the dead-worker override already short-circuits `owner_ok` for the
  non-blocked case, wq#1539).
- Traced every column the new UPDATE touches against the pre-existing generic-`open`
  UPDATE a few lines below (`wq:2644-2648`): same `claimed_by`/`claimed_at`/
  `lease_expires`/`lease_ttl`/`hold_by`/`hold_expires`/`lease_clear_sql()` set — the new
  branch is a strict subset (drops `status='open'`, `block_reason=NULL`, `blocked_at=NULL`)
  of the existing clearing semantics, not a new column-clearing policy invented for this
  ticket.
- Confirmed `dead_worker` is only true when `new_status == 'open'`
  (`wq:2567`), i.e. only the `release` command reaches this branch — `block`/`done` are
  unaffected, matching the ticket's stated scope.
- Ran the shipped suite directly against the live `/home/lumen/bin/wq`:
  `harness/tests/test_wq4463_release_dead_worker_blocked.py` (4/4 pass) and, with it,
  `test_wq1498_dead_worker_release.py` + `test_wq1539_release_dead_worker_order.py`
  (15/15 total pass) — matches the claimed count exactly.
- Checked all three keepers against the live binary independently of the shipped tests:
  `ensure_wq4463_release_dead_worker_blocked.py --check`, `ensure_wq_dead_worker_release.py
  --check` (wq#1498), `ensure_wq_release_dead_worker_order.py --check` (wq#1539) — all
  report every anchor `"applied"`. The wq#1498/wq#1539 anchor-string edits in `23554c8a`
  did not break those keepers, as claimed.
- **Independent manual repro, outside the shipped test harness**: built a scratch
  `WQ_DB`/stub `lumen-run` by hand, registered an orchestrator session, added+claimed+
  blocked a ticket with `agent=fworker-wqN` (so `holder_is_dead()` reads it as dead with
  no live session and no running unit), then called `wq release N --dead-worker` against
  the real `/home/lumen/bin/wq`. Result: `status=blocked` (unchanged), `claimed_by=NULL`
  (cleared), `block_reason="Frank: manual-repro block reason"` (preserved verbatim),
  stderr carries the new `"stayed blocked ... (wq#4463)"` notice. Matches the fix's claim
  exactly, independent of the author's own fixtures.
- **Regression-proved the test is not vacuous** (wq#2630 doctrine): extracted the pre-fix
  `wq` via `git -C /home/lumen/bin show b5e75b1~1:wq`, pointed `WQ_BIN` at it, and reran
  `test_wq4463_release_dead_worker_blocked.py`. `test_dead_worker_release_on_blocked_ticket_stays_blocked`
  and `test_the_live_wq_is_still_patched` **genuinely fail** against the pre-fix binary —
  the pre-fix `release` prints `"released 1"` (flips to open) instead of staying blocked,
  reproducing the original bug exactly. Cases B/C (non-blocked release, still-alive
  refusal) still pass pre-fix, as expected since the fix is additive and scoped to the
  blocked branch only.

## Adversarial probing — what I tried to break

- **Self-release of a blocked ticket by its own (live) holder**: `holder != caller` is
  false, so the override branch never triggers and falls to the pre-existing
  `owner_ok`/`block` paths untouched by this diff — out of scope for wq#4463, not a gap.
- **Fence race**: when no `--fence` is passed (the ordinary caller shape), `fence_guard`
  returns no predicate, same as every other `_finish` branch in this file — not a
  regression introduced here; a caller that does pass `--fence` still gets the
  `rowcount==0` ROLLBACK-and-refuse path on a stale fence, same as the generic paths.
- **Notes/block_reason overwrite**: confirmed the new branch never touches the `notes`
  column (unlike the ordinary `block` transition, which rewrites both `notes` and
  `block_reason` from `a.reason`) — the dead-worker-blocked path only clears
  claim/lease/hold columns, so existing notes are never touched, not just block_reason.
- Tried to find a path where the override branch's early `return 0` could skip
  `bump_reopened`/reopen bookkeeping incorrectly: status stays `'blocked'`, so
  `is_reopen(r[2], new_status)` would be false on the generic path anyway (blocked → open
  is the reopen case, not blocked → blocked) — returning `0` directly matches what the
  generic path would have computed had it run, no behavior lost.
- No break found in any of the above; the diagnosis, the fix's scope, and the keeper
  consistency all check out against the live binary, not just the diff text.

## Conclusion

wq#4463's diff does what it claims: `wq release --dead-worker` on a blocked ticket now
clears only the stale claim and leaves `status`/`block_reason`/`blocked_at` untouched,
while the non-blocked and still-alive-holder paths are provably unaffected. 15/15 shipped
tests pass against the live `/home/lumen/bin/wq`; all three related keepers report
patched; an independent manual repro outside the shipped harness reproduces the fix
exactly; and reverting to the pre-fix binary makes the two relevant tests fail for exactly
the original-bug reason, proving the regression test is non-vacuous (wq#2630). **CONFIRM**
— no escalation to opus-5@high warranted.
