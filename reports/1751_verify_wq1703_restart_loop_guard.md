# wq#1751 — adversarial verify-pass of wq#1703 (PR#448, spawn-loop guard)

**Verdict: CONFIRMED.**

## What was verified

PR#448 (`http://100.121.17.5:3000/lumen/claude-foreman/pulls/448`) adds
`dispatcher/spawn_loop_guard.py` plus a `foreman_dispatcher.py` integration (stage
3.5c) that stops re-spawning a ticket which reaches `FOREMAN_SPAWN_LOOP_MAX` (3)
dispatcher spawns inside `FOREMAN_SPAWN_LOOP_WINDOW_H` (3h) without a finished
`attempt-N.json` (ISSUE-LOG #283: wq#1543 spawned 31x, wq#1572 23x in 3h with no
artifact from either existing breaker).

Fetched the PR head directly (`git fetch origin refs/pull/448/head`), diffed it
against its merge-base with `origin/main` (`22de53d`), and read every changed line in
`dispatcher/spawn_loop_guard.py`, `dispatcher/foreman_dispatcher.py`,
`dispatcher/DESIGN.md`, and both test files cold, without trusting the PR description.

**PR#448's tree is byte-identical to commit `9fb0f9c`, already an ancestor of current
`main` (`4e9cf27`)** — `git diff 9fb0f9c FETCH_HEAD` is empty. The guard is already
live, not pending.

## Design check

- The core assumption — that a crashed worker leaves `self_heal.attempt_count()`
  unchanged — holds: `attempt-N.json` is written at terminal outcome only
  (`harness/fworker.py:1457`, `:835-836` — the failure-record path explicitly does
  *not* write `attempt-N.json`, "so the attempt is not charged"), never at spawn/start.
  So `record_spawn`'s `attempts` baseline and `looping()`'s later re-check of
  `attempt_count()` are comparing the same finished-work signal, not one polluted by
  the very spawn under test.
- `spawn_loop_guard.py:105-120` (`looping()`): threshold check (`len(entries) <
  threshold`) runs before the `attempt_count()` call, so the common non-looping case
  never touches the filesystem. Baseline is `min(attempts)` across in-window entries;
  since attempt counts are monotonic this equals the count at the oldest spawn — no
  off-by-one against the docstring's "reached N spawns without one attempt finishing."
- `foreman_dispatcher.py`: `spawn_loop` reason folded into
  `STRUCTURAL_SKIP_REASONS` (:163-169) so wq#1410's stall watchdog doesn't misread a
  working breaker as a stuck dispatcher; stage 3.5c (:1323-1330) sits before the
  epic/tracker check and record_spawn (:1514) fires right after `spawned += 1`, before
  `save_state` — ledger entries are only ever written for real spawns.
- `load_state()` (:352-395) seeds `spawn_loop_ntfy`/`spawn_ledger` on both the
  fresh-state and day-rollover-reset branches, and prunes the ledger via
  `spawn_loop_guard.prune_ledger` on every load — confirmed this survives UTC
  midnight (a naive reset would hand a looping ticket a fresh 3h budget every night;
  the ledger is intentionally NOT reset with `lane_counts`).
- `handle_spawn_loop` (:869-902) dedups the aq-row/ntfy pair per ticket per 24h but
  still `continue`s the skip on every pass regardless of dedup state — a re-check
  confirmed the ticket-refusal itself is never gated by the ntfy dedup, only the
  paging noise is.
- `aq_row` is fail-open (missing binary or non-zero exit both return `False` without
  raising) — correct per its own docstring: the skip has already happened by the time
  the alert-queue call runs, so a broken `aq` binary must not resurrect the loop.

No bug found in ledger persistence, dedup windows, baseline computation, or the
day-rollover/prune interaction.

## Reproduced test evidence (independent run, not trusted from the PR)

```
$ python3 -m pytest dispatcher/tests/test_spawn_loop_guard.py -q
18 passed in 0.13s

$ python3 -m pytest dispatcher/tests/test_dispatcher.py::test_repeated_spawns_with_no_finished_attempt_stop_and_open_an_aq_row \
    dispatcher/tests/test_dispatcher.py::test_a_held_ticket_is_not_re_filed_or_re_pinged_every_pass \
    dispatcher/tests/test_dispatcher.py::test_a_finished_attempt_lets_the_ticket_dispatch_again \
    dispatcher/tests/test_dispatcher.py::test_the_guard_holds_a_ticket_that_was_already_being_retried \
    dispatcher/tests/test_dispatcher.py::test_spawn_loop_skips_are_not_counted_as_dispatchable \
    dispatcher/tests/test_dispatcher.py::test_dry_run_neither_files_a_row_nor_pings \
    dispatcher/tests/test_dispatcher.py::test_the_ledger_survives_the_utc_day_rollover \
    dispatcher/tests/test_dispatcher.py::test_old_spawns_age_out_of_the_ledger -q
8 passed in 31.64s

$ python3 -m pytest dispatcher/tests/ -q
18 failed, 300 passed in 252.19s
```

All 18 unit tests and 8 integration tests specific to this guard pass. The full-suite
18 failures are pre-existing and unrelated: `test_self_heal.py`/`test_fire_drill.py`/
`test_dashboard_data.py::test_queue_depth_counts_open_eligible_blocked` reproduce the
exact wq#1478/wq#1676 liveness-gate class wq#1703's own author already logged at
ISSUE-LOG #299 (fixture calls `wq block` without `--liveness`/`--no-probe`/`--force`);
`test_burn_rate.py` (4) and `test_dispatcher.py::test_burn_rate_cap_replaces_binary_tight_clamp`/
`test_sec5_zero_floors_critical_engages_scaled_ceiling` are a newer, separate class from
the burn-rate-ceiling commits landed after wq#1703 closed (`c7cd075`, `b426460`,
`ac3cc17`) — none reference `spawn_loop` or touch `spawn_loop_guard.py`. Zero failures
are attributable to this PR's own code or tests.

## What I did not find

No adversarial break: could not construct a false-positive (an honest slow-but-working
ticket gets exempted the moment one attempt finishes, confirmed by
`test_a_finished_attempt_lets_the_ticket_dispatch_again`) or a false-negative (the
`test_the_guard_holds_a_ticket_that_was_already_being_retried` case — a ticket already
carrying finished attempts before the burst still gets caught unless the count moves
past the pre-burst baseline).
