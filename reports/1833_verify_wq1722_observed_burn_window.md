# wq#1833 — Adversarial verify-pass of wq#1722 (observed_burn.json writer: per-pool worker-hours window)

**Verdict: CONFIRM** — the fix (`dispatcher/observed_burn_writer.py`, commit `1f40db1`,
PR #517, already merged to `main`) is correct, minimal, and holds under independent
re-run, hand-derived arithmetic reproduction of the bug it fixes, and adversarial
edge-case tracing. No fabricated numbers, no regressions found.

## What was shipped

A one-line logic change plus supporting refactor in `poll_once()`
(`dispatcher/observed_burn_writer.py:191-219`): `since_dt`, a single datetime shared
across all pools (the min of every pool's last `sampled_at`, or a 1h lookback),
is replaced by `since_by_pool[pool]` — each pool's worker-hours window now starts at
**its own** last sample. The widest of the per-pool values (`earliest_since`) is still
used once to bound the single `journalctl`/`lumen-run` query (`gather_unit_series`),
which is safe because `observed_burn_core.worker_hours()` re-clips to each pool's own
`[since_by_pool[pool], now]` window via overlap math — fetching a wider raw unit list
doesn't leak into a narrower pool's computed hours.
`dispatcher/tests/test_observed_burn_writer.py` gains
`test_desynced_pool_does_not_widen_a_busy_pools_window` (+ the file's other 3 tests
were already passing pre-fix; this diff's actual behavior change is exercised only by
the new test).

## Claim-by-claim check

1. **Root cause reproduces under the OLD code, by hand** — traced
   `test_desynced_pool_does_not_widen_a_busy_pools_window`'s second poll (`t2 = t0+4h`)
   against the pre-fix logic: with a shared `since_dt = min(t0, t1) = t0` (fable frozen
   at `t0` in `insufficient_sample`), `session`'s `worker_hours` would integrate the
   running unit's overlap over the full `[t0, t2]` = 4h instead of its own `[t1, t2]` =
   2h. `delta_pct` (12→14) = 2, so old rate = 2/4 = 0.5 %/worker-h vs the correct 2/2 =
   1.0. `ema_update(prev_ema=1.0, rate=0.5, alpha=0.3)` = 0.3·0.5 + 0.7·1.0 = **0.85** —
   exactly the value the test's own comment predicts for the old, buggy path. This
   confirms the new test is a real regression test (not vacuous) and that the fix
   changes the outcome in the claimed direction and magnitude.

2. **Per-pool window correctly decouples busy vs. stalled pools** — confirmed by
   reading `poll_once` (`observed_burn_writer.py:198-207`): `since_by_pool[pool]` is
   computed once per poll from that pool's own `sampler_state[pool]["sampled_at"]`
   (unchanged for a pool stuck in `insufficient_sample`, since
   `update_pool_sample` returns `prev` verbatim — `observed_burn_core.py:121-122`,
   unchanged by this diff), and each pool's `worker_hours` call
   (`observed_burn_writer.py:219`) uses only its own since-value.

3. **Wide unit-series fetch does not leak into narrow pools** — `core.worker_hours`
   (`observed_burn_core.py:62-84`) clips every unit's contribution to
   `[window_start, window_end]` via `overlap = min(end, window_end) - max(start,
   window_start)`, independent of how far back `unit_series` itself was fetched.
   Fetching once with `earliest_since` (`observed_burn_writer.py:206-207`) is therefore
   a pure efficiency choice (one journalctl/lumen-run round-trip covers every pool),
   not a correctness dependency — traced this explicitly since it's the one place a
   shared value still exists post-fix.

4. **`seed`/`reset_crossed`/`no_burn`/`updated` event handling is untouched** — diffed
   `observed_burn_core.py` against its pre-image: zero changes. All of
   `update_pool_sample`'s branches (including the `insufficient_sample` freeze this fix
   depends on) are pre-existing, already-tested behavior from wq#1722's initial land,
   not introduced or altered by this diff.

5. **No stale references** — `grep -rn since_dt dispatcher/` after the diff finds only
   the (accurate) explanatory comments in `observed_burn_writer.py` and the new test;
   no code path still reads the old variable name.

6. **Full targeted suite, run directly**:
   - `python3 -m pytest dispatcher/tests/test_observed_burn_writer.py
     dispatcher/tests/test_observed_burn_core.py -v` → **16/16 passed**, including the
     new desync-window test and the pre-existing seed/fable-split/reset-crossing cases.
   - `python3 -m pytest dispatcher/tests/test_burn_rate.py -q` → 4 failed / 8 passed.
     The 4 failures (`test_sec5_case_opens_past_the_old_tight_clamp`,
     `test_cap_grows_toward_ceiling_as_reset_approaches`,
     `test_mid_session_is_not_clamped_to_one`,
     `test_fable_pool_narrows_only_the_fable_ceiling`) are the **same** pre-existing,
     already-logged failures from `kb/ISSUE-LOG.md` #329 (fixtures predate the
     unrelated `FRANK_RESERVE_PCT` change in `pool_spend_rate`) — confirmed by matching
     the exact assertion values (`assert 1 == 3`, `assert 1 == 10`, etc.) against the
     logged root cause. Nothing in wq#1722/wq#1833's diff touches `pool_spend_rate` or
     `FRANK_RESERVE_PCT`.

## Not re-litigated

- The performance characteristic that a permanently-stalled pool (e.g.
  `fable_weekly_scoped` with zero fable tickets for days) causes the shared
  `journalctl --since` query to widen indefinitely is **pre-existing** — the original
  code already computed the same global `since_dt` for the journalctl query. This diff
  only changes the per-pool *math* window, not the query-fetch window. Out of scope for
  this verify-pass.
- `dispatcher/tests/test_burn_rate.py`'s 4 failing fixtures (item 6 above) — already
  logged, tracked as a separate follow-up ticket need in `kb/ISSUE-LOG.md` #329.

## Reproduce

```
python3 -m pytest dispatcher/tests/test_observed_burn_writer.py dispatcher/tests/test_observed_burn_core.py -v
python3 -m pytest dispatcher/tests/test_burn_rate.py -q   # 4 pre-existing unrelated failures, see ISSUE-LOG #329
```
