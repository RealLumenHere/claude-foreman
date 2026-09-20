# wq#2135 — lumen_state supersede raised UNIQUE IntegrityError

## Root cause

Not the reconfirm/prefix-split branch, and not a same-day valid-from collision.
23 `state_facts` rows in `minerva.db` had `id IS NULL` (the 2026-09-12T06:00:14
batch, plus two from 2026-09-14), including the open `nephthys.availability`
row. `id TEXT PRIMARY KEY` accepts NULL in SQLite. `_ingest` closes the
superseded row with `UPDATE ... WHERE id=?`, which never matches NULL, so the
old row stayed open and the following INSERT collided with
`idx_sf_one_open` (`UNIQUE(subject, predicate) WHERE valid_to IS NULL`):
`UNIQUE constraint failed: state_facts.subject, state_facts.predicate`.

The same NULL-id rows also made reconfirm a silent no-op (its UPDATE is keyed
on `id` too), so those facts never refreshed `last_confirmed_at`.

## Fix (class, not instance)

`_heal_null_ids()` in `/home/lumen/minerva/lumen_state.py:233-250`, called from
`_migrate()` (`:231`), which every ingest/current path runs:

- backfills a uuid-shaped id for every NULL-id row;
- adds `trg_state_facts_id_default`, so any writer that omits `id` gets one.

The trigger's presence gates the work; an already-healed DB pays one SELECT.
`lumen_state.diff` is the same change as a diff (minerva/** is not a git repo).

## Tests

`test_null_id_supersede.py` (5 tests, run against the real module with a temp
DB): supersede over a NULL-id open row, reconfirm over one, backfill,
trigger, idempotence. With `_heal_null_ids` neutralised the supersede test
fails with the exact production `IntegrityError`; with the fix it passes.
wq1689 + wq1632 suites still pass (19).

## Live re-ingest (2026-09-20)

Applying the fix healed all 23 rows (0 NULL ids remain). Then:

- `nephthys availability online` (valid-from 2026-09-20) -> `superseded`.
- `nephthys lifecycle active` -> `superseded` (the wq#1700 gate refuses
  operational facts on a decommissioned subject; wq#2127 found the box up and
  in the LiteLLM `local-worker` group, so it is back in service).
- `nephthys ollama_serving qwen3:14b`, note `32768 ctx x 2 slots; member of
  LiteLLM group local-worker`.
