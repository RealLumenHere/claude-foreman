# wq#1855 — Adversarial verify-pass of wq#1815 (dispatcher double-spawn: adopt despag control_sqlite)

**Verdict: CONFIRM** — commit `54861ab` `feat(dispatcher): reserve ticket+attempt spawns
(wq1815)` (currently HEAD of `/home/lumen/foreman` main) closes the gap PHASE0_WIRING_SPEC.md
E.5 names ("no per-ticket reservation that makes a second spawn for the same ticket+attempt
impossible by construction") and does so correctly. Read the diff cold, reconstructed the
negative control independently, ran the shipped test 8x for flakiness, and ran the wider
regression suites the change touches.

## What was shipped

`harness/spawner.py` gains `reserve_spawn()`/`release_spawn()` (lines 99-117), wired into
`dispatch_one()` (142-172): before exec'ing `lumen-run start`, it computes
`attempt = self_heal.attempt_count(ticket_id) + 1` (read-only — counts existing
`attempt-N.json` files, unaffected by the reservation itself) and calls despag's
`ControlStore.idem_reserve()` (wq#1807, `despag/backing/control_sqlite.py:82-103`) keyed
`dispatch-spawn:<ticket>:<attempt>`. The loser gets back the winner's record (non-`None`)
and returns `None` before `Popen`; a spawn that fails after winning the reservation
(`lumen-run` OSError/timeout/non-zero rc) calls `release_spawn()` so the next pass can
retry the same attempt number instead of being locked out for the full TTL.

Reused, not reimplemented: `idem_reserve`/`idem_release` predate this diff (wq#1807). The
new code is the ~19-line reserve/release pair plus one `if not reserve_spawn(...): return
None` gate in `dispatch_one`, `test_double_spawn_reservation_blocks_racing_duplicate`
(harness/tests/test_spawner.py:146-173), and the `evidence.json` AC1 pointer.

## Why the atomicity actually holds

- `ControlStore._connect()` (`despag/backing/control_sqlite.py:45-52`) opens WAL mode with
  `busy_timeout=30000`; every write happens inside one `BEGIN IMMEDIATE` (`idem_reserve`,
  lines 88-97), so a second writer blocks on the lock rather than racing a read-then-write
  window. This is a cross-process guarantee (sqlite file, not an in-memory dict), which
  matters because the race E.5 describes is cross-process ("two dispatcher passes ... or a
  dispatcher restart mid-pass"), not just cross-thread within one Python process.
- `attempt_count()` (`dispatcher/self_heal.py:281-288`) is independent of the reservation —
  it lists `attempt-N.json` files on disk, so two racing calls compute the *same* attempt
  number (neither has created that file yet), which is what makes them collide on the same
  reservation key instead of silently getting different keys.

## Method — reconstructed the negative control, didn't take the PR's word for it

Checked out `54861ab^` (the commit immediately before this fix) into a scratch worktree,
copied the shipped `test_double_spawn_reservation_blocks_racing_duplicate` onto the
pre-fix `spawner.py` (which has no `reserve_spawn`/reservation gate), and ran it:

```
AssertionError: expected exactly one lumen-run start call, got 2
```

Confirms the test exercises a real vulnerability (pre-fix code double-spawns under this
exact race) and that the fix is what closes it — not a test that would have passed anyway.
Scratch worktree removed after (`git worktree remove --force`); `/home/lumen/foreman`'s own
checkout was never touched (`git status` clean throughout, verified after).

Ran the shipped test 8x in a row against the post-fix code (real `threading.Thread` +
real `subprocess.run` on a fake `lumen-run`, not a mocked race) — 8/8 pass, no flake:

```
harness/tests/test_spawner.py::test_double_spawn_reservation_blocks_racing_duplicate  PASSED  x8
```

Full `test_spawner.py`: 7 passed. `despag/tests/test_backing.py test_control.py
test_uhp.py` (the suite evidence.json's prior AC cited): 64 passed.

Wider regression check: `harness/tests/test_fworker_integration.py` has 2 pre-existing
failures (`test_denied_verb_blocked_at_hook_during_real_spawn`,
`test_launch_failure_blocks_with_infra_crash_class`, both `assert 'claimed' == 'blocked'`)
that are **unrelated** — reproduced identically against `54861ab^` (pre-wq1815) in the same
scratch worktree, and the file has zero references to `spawner` at all. Not a regression
from this diff; not this ticket's to fix.

## One observability nit, not a correctness bug

`dispatcher/foreman_dispatcher.py:1506-1511` (unchanged by this diff) treats every
`spawner.dispatch_one() -> None` the same way: logs `reason="spawn_failed"` and fires an
`ntfy` alert. That call site predates wq#1815 and already collapsed governor-deny /
routing-failure into the same generic path; this diff adds one more `None` reason
(reservation lost) to it. A blocked duplicate spawn is the *reservation working as
intended*, not a failure, so the alert text is misleading in that one case — worth a
follow-up ticket to give `dispatch_one` a richer return (e.g. an enum/reason string) so the
caller can log "duplicate spawn blocked" distinctly from a real launch failure. Since a
cross-process double-dispatch race is itself anomalous, alerting on it isn't wrong, just
mislabeled. Doesn't change the verdict: no double spawn occurs either way.

## Commands run

```
python3 -m pytest harness/tests/test_spawner.py -q                                   # 7 passed
python3 -m pytest despag/tests/test_backing.py despag/tests/test_control.py \
  despag/tests/test_uhp.py -q                                                        # 64 passed
python3 -m pytest harness/tests/test_fworker_integration.py -q                       # 22 passed, 2 pre-existing fails
# negative control (pre-fix spawner.py + post-fix test, scratch worktree at 54861ab^):
python3 -m pytest .../test_spawner.py::test_double_spawn_reservation_blocks_racing_duplicate -q
  # -> AssertionError: expected exactly one lumen-run start call, got 2
```
