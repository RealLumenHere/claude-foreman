# wq#4310 — JSON-fallback outcomes now merge back into PG

## What

`harness/recipe_store.py` grows a journal of the outcomes a PG outage pushed into the
JSON fallback, and drains them back into PG on the first successful write afterwards.
`recipe_store.py drain [--seed-missing]` exposes the same for operators and repairs
cells orphaned before the journal existed. The docstring's fail-open overclaim is
replaced with the guarantee the code now actually makes.

## Why

wq#4304's verify-pass CONTRADICTED wq#4288: `record_outcome()` fell back to the JSON
store on any PG failure, but the only JSON→PG path (`seed_from_json()`,
`ON CONFLICT DO NOTHING`, called only by the operator-run `migrate` CLI) cannot see a
delta on a cell PG already has a row for. An outcome recorded during a PG hiccup landed
in a file `candidate_model()` never reads — indistinguishable from dropped, contradicting
the docstring's "a PG outage drops no counter updates on the floor". Live proof:
`dispatcher/recipes.json` had three `config-edit` cells, the PG table two;
`claude-opus-5-5::rca-investigation` (streak 1, wins 1) was invisible to
`rates config-edit`.

The ticket offered "add a reconciler" or "accept the bounded blast radius and fix only
the docstring". This takes the reconciler. The blast-radius argument is sound about
*freezes* (a missed freeze falls back to `router.assign`, never a wrongly-pinned route)
but the loss is in the *counters*, and wq#4288 exists to make per-model win tracking
authoritative. Merge-on-read was rejected on correctness: it has only the JSON aggregate
to work from, so it double-counts migrated cells, and it would let JSON state into the
freeze decision the design deliberately forbids. A systemd timer was rejected as the
drain trigger: same bookkeeping, plus a unit that can silently stop and a lag window.
Reasoning in full: `reports/wq4310_judgment_json_fallback_reconciler.md`.

## How

- Each fallback outcome also appends to `recipes.json.pending.jsonl` (outbox pattern).
- `drain_journal()` replays journaled outcomes through `_record_pg()` in recorded order,
  dropping an entry only once its PG transaction commits. Replaying the outcome
  *sequence* rather than diffing aggregates keeps streak semantics exact — a journaled
  loss still breaks the streak where it happened.
- `record_outcome()` drains before its own PG write, so the first successful close after
  an outage heals the gap. No timer to forget to run.
- One transaction per `ticket_type` per drain: `_record_pg()` takes a per-type advisory
  lock, and one transaction holding several could deadlock against a concurrent close.
- `DRAIN_MAX = 200` bounds what the first close after a long outage pays; the remainder
  drains on the next close.
- `FOREMAN_RECIPE_PG=off` never journals — a deliberate JSON backend is not an outage,
  and journaling it would double-count against a later `migrate` seed.
- `_journal_append()` swallows its own errors: worst case leaves exactly the
  pre-wq#4310 behaviour, never a failed close.

## Testing

9 new cases in `harness/tests/test_recipe_store.py` (5 fail-open, 4 `needs_pg`).
`20 passed / 10 skipped` without PG; `22 passed` with `FOREMAN_RECIPE_PG_TEST=1` against
real fleet_memory in scratch `recipes_test_<hex>` tables, nothing skipped.

Negative control: with `FOREMAN_RECIPE_JOURNAL` pointed at an uncreatable path — i.e.
pre-wq#4310 behaviour — 8 of the 9 new cases FAIL (the ninth asserts `PG=off` writes no
journal and is correctly indifferent). The probes can fail.

Live repair, outside the fixture population: `drain --seed-missing` ran against
fleet_memory and seeded exactly the one orphaned opus row; `claude-sonnet-5` stayed at
wins 5, so the cells PG already knew were not re-seeded over themselves; a re-run seeds
0.

## Risk / rollback

Low. The journal is additive and only written on a real PG failure; the drain is a
no-op behind one `stat` when the journal is absent, which is the steady state. A drain
failure leaves every entry pending for the next attempt rather than half-counting, and
a journal that cannot be written degrades to the previous behaviour. Rollback is
reverting this commit; a leftover `recipes.json.pending.jsonl` is then inert and can be
deleted. The live `--seed-missing` repair inserted one row and is not reverted by a
code rollback — delete that row manually if the repair itself needs undoing.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
