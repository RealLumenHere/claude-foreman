# wq#4304 — verify-pass of wq#4288 (recipe_crystallizer freeze threshold + fleet_memory PG store)

**Verdict: CONTRADICT.** Escalated to wq#4310 (claude-opus-5@high, judgment, deps=4288).

## What wq#4288 shipped

`/home/lumen/foreman` commit `bf297b36` ("fix(dispatcher): freeze recipes at 5 and
store in fleet_memory", PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2200):

- `dispatcher/recipe_crystallizer.py`: `FREEZE_THRESHOLD` default 3 → 5, plus
  `reconcile_freezes()` (flock'd, unfreezes sub-threshold freezes, streak intact).
- `harness/recipe_store.py` (new, 360 lines): fleet_memory PostgreSQL backend, same
  public API as `recipe_crystallizer` (`lookup_recipe`/`is_frozen`/`record_outcome`/
  `load_store`), PK `(ticket_type, model, prompt_template)`, `migrate` CLI
  (pg_dump → additive DDL → JSON reconcile → seed PG from JSON → PG reconcile).
  `dispatcher/foreman_dispatcher.py` and `harness/fworker.py` switched from
  `recipe_crystallizer` to `recipe_store` as the live call site.
- Docstring claim (`recipe_store.py:19-24`): *"record_outcome(): on any PG failure
  the outcome goes to recipe_crystallizer's JSON store instead, so a PG outage
  drops no counter updates on the floor."*
- Tests: `dispatcher/tests/test_recipe_threshold_wq4288.py` (7 tests, JSON side),
  `harness/tests/test_recipe_store.py` (13 tests incl. `needs_pg`-gated real-PG
  cases). Fixture `recipes_wq4288_live_freeze.json` replays the actual live
  streak-3 freeze fleet-ops2 found.

Ticket AC (wq#4288, as recorded on the board): threshold defaults to 5; live
streak-3 freeze gets reconciled under the store flock with streak intact; PG-backed
store exists with the same API, fail-open, JSON fallback; migration backs up first,
additive DDL only.

## What I checked

1. Read `dispatcher/recipe_crystallizer.py` and `harness/recipe_store.py` in full
   cold. `record_outcome()`/`_record_pg()` parity, `reconcile_freezes()`/
   `reconcile_pg()` parity, and the advisory-lock serialization all check out line
   by line against the JSON semantics.
2. Ran the committed suites: `python3 -m pytest dispatcher/tests/test_recipe_threshold_wq4288.py harness/tests/test_recipe_store.py -q`
   → 15 passed, 6 skipped (the `needs_pg` cases, gated on `FOREMAN_RECIPE_PG_TEST=1`).
   Re-ran with that flag set (scratch `recipes_test_<hex>` table, dropped at
   teardown): 13 passed, including the threshold-5 boundary, single-loss unfreeze,
   one-frozen-per-type, concurrent `record_outcome`, live-freeze migration, and PG
   reconcile cases. AC as written and every committed test is satisfied — this is
   not a test-coverage gap.
3. Tried to break it against the **real, already-migrated** live state rather than
   a synthetic fixture (migration ran for real: `dispatcher/recipes.json` mtime
   `2026-10-18T21:38:53Z`, 28s after the commit).
   - `python3 harness/recipe_store.py show` (PG path) →
     `config-edit` cells: `claude-sonnet-5::mechanical-edit` (streak 1) and
     `claude-sonnet-5::standard-engineering` (streak 3, wins 4), `frozen: {}`.
   - `cat dispatcher/recipes.json` (JSON fallback path, read-only) → **also has**
     `claude-opus-5-5::rca-investigation` under `config-edit`, streak 1, wins 1 —
     a cell PG has never seen.
   - Confirmed directly: `SELECT ticket_type, model, prompt_template, wins, losses,
     streak, frozen FROM recipes` returns exactly 2 rows (the two `claude-sonnet-5`
     cells). No `claude-opus-5-5` row exists, in any ticket_type.
   - `recipe_store.py rates config-edit` → one row, `claude-sonnet-5`, `wins: 5`
     (1 + 4). The opus cell's win is invisible to the "per-model success rate" view
     R1/wq#4288 asked for.

## Why this matters

`harness/recipe_store.py` has exactly one path that writes JSON
(`record_outcome()`'s `except` branch, routing to `_json_store.record_outcome()` on
*any* PG failure) and exactly one path that reads JSON back into PG
(`seed_from_json()`, called only from `migrate()`, `ON CONFLICT DO NOTHING`, and
only ever invoked by the operator CLI — grepped the tree, no timer/service calls
`recipe_store.migrate` or `seed_from_json`). There is no code path that re-attempts
or re-seeds a JSON-fallback write once PG recovers. A cell outcome recorded during
*any* PG hiccup (connect timeout, container restart, the `PG_RETRY_S=60` skip
window) is captured — the ticket's own done/block path is correctly never
blocked — but it is captured **only** in the JSON file, which `candidate_model()`
never reads once `recipe_store` is the live backend (`recipe_crystallizer.py`'s own
updated docstring: *"Nothing reads JSON freezes while PG is the backend"*). The
counter update is not on the floor, as the docstring claims — it is in a file
nothing will ever read again, which is observably indistinguishable from dropped:
it never counts toward that cell's streak, never contributes to `success_rates()`,
and the only way back is a human manually diffing `recipes.json` against the PG
table and re-running a migration-shaped operation that doesn't currently exist as
a general tool (`seed_from_json` assumes a from-scratch migration, not a periodic
reconciliation — it has no "only cells newer than the last sync" cursor).

This is not a hypothetical: the `claude-opus-5-5::rca-investigation` cell is real,
live data that exists today only in the JSON fallback, orphaned from the PG table
the whole rest of the system now treats as authoritative. The worst-case blast
radius is bounded (a missed/delayed freeze defaults back to `router.assign`, never
a wrongly-pinned route), so this is not a dispatch-correctness emergency — but it
is a direct contradiction of the shipped code's own fail-open guarantee, and it
silently degrades the exact per-model win-tracking this ticket exists to make
authoritative.

## Disposition

- `wq#4304` closed with notes citing this report and `wq#4310`.
- `wq#4310` filed: `[judgment] wq#4288 recipe_store JSON-fallback outcomes never
  merge back to PG — permanent streak/counter loss`, model `claude-opus-5`,
  effort `high`, `deps=4288`, `--no-probe` (scope/design judgment call: whether to
  add a periodic reconciler, merge-on-read, or accept and fix only the docstring's
  overclaim, not a deterministic pass/fail probe).
- `kb/ISSUE-LOG.md` appended via `issue_log_append.py`.
- No re-implementation attempted here per archetype 8.
