# wq#4578 (3/3) — make the phase-2 installer re-runnable so the renderer fix can actually deploy

Follow-up to PR #2416 and #2419. Deploying the parser fix means re-running
`ops/wq4554-decisions-migration/install.sh`, because the live renderer is untracked at
`/home/lumen/minerva/memory/context_builder.py` — re-install *is* the deploy path. That run died
before it got there.

## What

- `ops/wq4554-decisions-migration/migrate_decisions.py` — new `_source_table(cur, name)`:
  `to_regclass` probe that reads the live table when it exists, falls back to the frozen
  `retired_<name>` copy when the installer's own freeze step has already run, and returns `None`
  (skip) when neither exists.
- `ops/wq4554-decisions-migration/test_decisions_migration.py` —
  `test_source_table_falls_back_to_frozen_copy`, a fake-cursor unit test (no PG needed).
- `reports/wq4578_judgment_state_line_since_collision.md`, `pr_body_wq4578c.md` — the record.

## Why

`install.sh` step 3 SELECTs PG `fleet_decisions`/`fleet_facts`; its own step 5 renames both to
`retired_*`. So the second run of the installer exits 1 with
`psycopg2.errors.UndefinedTable: relation "fleet_decisions" does not exist` before reaching the
install/render/verify steps — the bundle was a one-shot that left no way to ship any later change
to the renderer it installs. `kb/ISSUE-LOG.md` row 1552.

Reading the frozen copy is safe and is the honest resync: the mirror inserts are
`INSERT OR IGNORE` keyed on id, and the `retired_*` tables hold exactly the rows the first
migration copied. PR #2419 already made the freeze step itself idempotent
(`ALTER TABLE IF EXISTS`); with this change the installer is idempotent end to end.

## How

Two candidate names per source table, both literals in this file (no caller-supplied identifier
reaches the SQL), probed with a parameterized `SELECT to_regclass(%s)`.

## Testing

- `python3 -m pytest -q ops/wq4554-decisions-migration/test_decisions_migration.py` → **4 passed**.
- `python3 -m pytest -q ops/wq4553-context-builder-live-roster/test_live_roster.py` → **9 passed**.
- `python3 scripts/wq4578_outside_probe.py` → exit 0 for both bundle copies.
- The deploy itself: `lumen-run start --name wq4578-apply3 -- bash .../wq4554-decisions-migration/install.sh`,
  then `install.sh --verify` → the live renderer carries the greedy value group.

## Risk / rollback

Low. The change only widens where the migration *reads* from, and only when the live table is
already gone; a fresh fleet (live tables present) takes the identical path it took before.
Rollback: revert this commit — the installer returns to its one-shot behaviour.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
