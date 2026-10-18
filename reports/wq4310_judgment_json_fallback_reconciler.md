# wq#4310 — judgment on wq#4304's CONTRADICT of wq#4288

**Decision: fix it, don't downgrade the docstring.** `harness/recipe_store.py` grows a
JSON-fallback journal (the outbox pattern) that drains into PG on the first successful
write after an outage, plus a `drain --seed-missing` repair for cells orphaned before
the journal existed. The live orphan wq#4304 found is repaired.

## The three options, and why this one

wq#4304's finding stands: `record_outcome()`'s fallback wrote the outcome to the JSON
store, and the only JSON→PG path (`seed_from_json()`, `ON CONFLICT DO NOTHING`, called
only by the operator `migrate` CLI) cannot see a delta on a cell PG already has a row
for. So an outcome recorded during any PG hiccup was captured in a file
`candidate_model()` never reads — indistinguishable from dropped.

1. **Docstring-only ("accept the bounded blast radius").** Cheapest, and the blast
   radius argument is sound as far as it goes: a missed freeze falls back to
   `router.assign`, never a wrongly-pinned route. Rejected anyway, because the loss is
   not in the *freeze* decision, it is in the *counters*. wq#4288 exists to make
   per-model win tracking authoritative (spec R1); a store that silently undercounts
   wins by an unknown amount is not authoritative, and the undercount is invisible —
   nothing in `rates` says "and 1 win is in a file over there". Writing the limitation
   into the docstring makes the code honest and the data no better.
2. **Merge-on-read.** Rejected on correctness, not cost. A read-time merge has only the
   JSON *aggregate* to work from and no way to tell which part of it PG already counted,
   so it double-counts every migrated cell. It would also have to let JSON state into
   the freeze decision, which the current design explicitly forbids ("the fallback
   cannot pin a route") and which is the property that keeps a PG outage harmless.
3. **A journal drained on the next successful write (chosen).** Each fallback outcome is
   appended to `recipes.json.pending.jsonl`; `drain_journal()` replays those entries
   through `_record_pg()` in recorded order and drops them only once the PG transaction
   commits. `record_outcome()` drains before its own write, so recovery needs no timer,
   no operator, and no new service — the next closed ticket of any crystallizable type
   heals the gap. Replaying the *outcome sequence* rather than diffing aggregates is
   what makes streaks and freezes land exactly as they would have live: a journaled
   loss still breaks the streak at the point it happened
   (`test_pg_drain_replays_in_recorded_order_not_as_an_aggregate`).

A periodic systemd timer was considered as the drain trigger and dropped: it needs the
same journal bookkeeping anyway, adds a unit that can silently stop, and leaves a lag
window the on-write drain does not have. `recipe_store.py drain` covers the operator
case for a fleet that has stopped closing crystallizable tickets entirely.

## Design points worth knowing

- **`FOREMAN_RECIPE_PG=off` never journals.** Off is a deliberate JSON backend (the test
  suite's root conftest sets it), not an outage. Journaling it would accumulate entries
  that a later `migrate` would replay *on top of* its own seed of the same cells —
  double-counting. Only a real PG failure journals.
- **One transaction per ticket_type in a drain.** `_record_pg()` takes a per-ticket_type
  advisory lock; one transaction holding several could deadlock against a concurrent
  close, one lock per transaction cannot. A group whose transaction fails stays in the
  journal — the increments and the entry's removal commit or roll back together, so a
  replay is never half-counted.
- **`DRAIN_MAX = 200`** bounds what the first close after a long outage pays; the
  remainder drains on the next one.
- **The journal never fails a close.** `_journal_append()` swallows its own errors: the
  fallback path's job is to not lose the ticket, and a worse-case journal failure leaves
  exactly the pre-wq#4310 behaviour.

## Live repair

The orphan is real data, so it got fixed rather than just described. Before:
`dispatcher/recipes.json` had 3 `config-edit` cells, the `recipes` PG table had 2 —
`claude-opus-5-5::rca-investigation` (streak 1, wins 1) existed only in JSON and
`rates config-edit` showed only `claude-sonnet-5`.

    python3 harness/recipe_store.py drain --seed-missing   -> {"seeded": 1, drained: 0 pending}
    python3 harness/recipe_store.py rates config-edit      -> claude-opus-5-5 wins 1
                                                              claude-sonnet-5  wins 5
    python3 harness/recipe_store.py drain --seed-missing    -> {"seeded": 0}   (idempotent)

`claude-sonnet-5` stayed at 5, so the two cells PG already knew were not re-seeded on
top of themselves.

## Evidence

- `harness/recipe_store.py:245-353` journal + `drain_journal()`;
  `:365-366` the drain hook in `record_outcome()`; `:378` the fallback journal append;
  `:19-38` the docstring now describing the real guarantee.
- 9 new cases in `harness/tests/test_recipe_store.py` (5 fail-open, 4 `needs_pg`).
  Full suite: 20 passed / 10 skipped without PG; 22 passed with
  `FOREMAN_RECIPE_PG_TEST=1` against the real fleet_memory (scratch
  `recipes_test_<hex>` tables, dropped at teardown), nothing skipped.
- Negative control (`/tmp/fworker-wq4310/negative_control.py`): with
  `FOREMAN_RECIPE_JOURNAL` pointed at an uncreatable path — i.e. pre-wq#4310 behaviour —
  8 of the 9 new cases FAIL. The ninth asserts that `PG=off` writes no journal, which is
  correctly indifferent. The probes can fail.
