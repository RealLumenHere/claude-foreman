# wq#1897 — Adversarial verify-pass of wq#1893 (foreman: spawn reservation outlives a dead worker)

**Verdict: CONFIRM.** PR #567 (commit `c4be28d` on `main`, based on `22d137e`) does
what it claims: a dead spawn-reservation owner is reclaimed in the same dispatcher
pass instead of blocking the ticket's attempt number for the full 6h TTL, a live
owner still refuses unchanged, and a landed attempt clears its own reservation
immediately. Read the diff cold, tried to break the reclaim/settle logic against the
actual `ControlStore`/`concurrency` APIs (not the docstrings' description of them),
and ran the suite.

## What was checked

Diff: `harness/spawner.py` (+`reservation_owner_alive`, +`DEAD_OWNER_GRACE_S`,
reclaim branch in `reserve_spawn`, new `settle_spawn`), `harness/fworker.py`
(`write_attempt_state` calls `_settle_spawn_reservation`), two new test files'
worth of cases, `evidence.json` AC1 retargeted.

- **Citation check**: `evidence.json` AC1 cites `harness/spawner.py:141-167`. That
  range is exactly `reserve_spawn` end-to-end, the fix's core function — exact match,
  not off-by-a-few like the last verify pass caught elsewhere.
- **Exact-name liveness check, not prefix match**: `reservation_owner_alive` does
  `f"fworker-wq{ticket_id}" in units` against `concurrency.list_running_units()`'s
  return value. Read `concurrency.py`'s `UNIT_RE`/`TICKET_UNIT_RE` and
  `list_running_units` body directly (not just its docstring): it already strips
  `lumen-job-<name>-<epoch>.service` down to bare `fworker-wq<ID>` before returning,
  so the `in` check is exact list-membership against clean names, not a substring
  test — the docstring's wq#1893-vs-wq#18930 collision claim is actually true, it
  isn't just an unverified assertion.
- **Fail-closed on `lumen-run` trouble**: `reservation_owner_alive` catches
  `concurrency.LumenRunError` and returns `True` (assume alive) — matches wq#1498's
  `holder_unit_running` convention cited in its own docstring; a transient
  `lumen-run list` failure cannot cause a false reclaim of a live worker.
  Independently re-read `LumenRunError`'s raise sites in `concurrency.py` to confirm
  every `lumen-run` failure mode (non-zero exit, OSError, timeout) actually raises it
  rather than e.g. returning an empty list.
- **Race safety of the reclaim itself**: `reserve_spawn`'s reclaim path does
  `idem_release(key, existing_resp_id)` then re-`idem_reserve`s under the new owner,
  and if that second reserve finds someone else already got there first, defers
  (`return False`) rather than stealing. Read `despag/backing/control_sqlite.py`'s
  `idem_release`/`idem_reserve` bodies directly: `idem_release` only deletes a row
  whose `resp_id` still matches and whose `state == "reserved"`, so a concurrent
  reclaim by another dispatcher pass can't be clobbered — the "defer to them" comment
  is backed by the actual locking, not just asserted.
- **Grace period**: `DEAD_OWNER_GRACE_S=60` guards a reservation younger than that
  from being judged at all, regardless of `reservation_owner_alive`'s answer — covers
  the real gap between `reserve_spawn` returning `True` and the `lumen-run start`
  subprocess (30s timeout) actually registering as `running`. Consistent with the
  wq#1883 incident data cited in the diff (5 passes ~4-5min apart, i.e. 60s is well
  inside one pass interval, not a race against it).
- **Settle-on-write correctness**: `_settle_spawn_reservation` in `fworker.py` is
  wrapped in a broad `except Exception` that only logs — a bookkeeping failure on the
  reservation table cannot fail an attempt that already landed. `settle_spawn` reads
  the row's current `resp_id` then releases exactly that `resp_id`; per
  `idem_release`'s guard above, a reclaim that reused the key between the read and
  the release is not clobbered.
- **Independent test run**: checked out the PR head (`c4be28d`) detached in this
  verify worktree (own clone, not the author's run). `harness/tests/test_spawner.py`:
  **10/10 pass** (all pre-existing plus the 3 new: reclaim-from-dead-owner,
  refuse-when-still-running, settle-clears-reservation). New integration test
  `test_success_settles_spawn_reservation` in `test_fworker_integration.py`: **pass**
  in isolation and inside the full file.
- **Regression check**: full `test_fworker_integration.py` run at PR head is
  **16/18** — the 2 failures
  (`test_denied_verb_blocked_at_hook_during_real_spawn`,
  `test_launch_failure_blocks_with_infra_crash_class`, both `assert 'claimed' ==
  'blocked'`) reproduce **identically** after `git checkout --detach 22d137e` (the
  PR's own parent, no wq#1893 changes present) — confirmed pre-existing and
  unrelated before trusting the diff as clean. Logged for the record; not this
  ticket's root cause to chase.

## Known gap found (non-blocking, environmental)

Tried to append the pre-existing-failure finding above to `/home/lumen/kb/ISSUE-LOG.md`
per doctrine; `guard.sh` blocks writes to that primary tree from this worktree (same
class of block wq#1886's verify pass already hit and documented — kb has no
per-ticket worktree wired for BUILD workers). Recording it here instead rather than
forcing the write or burning attempts on it.

## Bottom line

The reclaim path checks real liveness (exact-name match against actual running
units, fail-closed on `lumen-run` trouble), the grace period is sized against the
actual dispatch timing it's protecting, the reclaim race is closed by
`idem_release`'s own resp_id+state guard rather than by convention, and the settle
path can't fail a landed attempt. Own new tests pass 10/10 and 1/1; the only test
file with failures fails identically before the diff exists. Confirming wq#1893 as
delivered.
