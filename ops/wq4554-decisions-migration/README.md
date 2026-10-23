# wq#4554 — wq#4539 phase 2: one truth — fleet_decisions (+fleet_facts) into minerva.db

Source: `design/wq4539_one_render_one_truth.md` phase 2. Depends on phase 1 (wq#4553,
landed) and phase 0/QW5 (landed 2026-10-22).

## What this bundle ships

- `schema.sql` — `fleet_decisions` and `fleet_facts` tables in minerva.db, mirroring the
  PG columns (`fleet_decisions` plus `superseded_by`/`superseded_ts`, derived from the
  reverse of PG's `supersedes` column — PG records "this row supersedes X", not the
  other way).
- `migrate_decisions.py` — `pg_dump`-backed copy of PG `fleet_decisions` and `fleet_facts`
  into minerva.db, idempotent (checked per-row before insert), each insert audited via an
  append to minerva's own `memory_audit` ledger (the mechanism memoryd's own writers use
  for trust/audit — no separate memoryd RPC client exists in this lane's accessible code;
  `minerva/memory/fm_db.py`'s `log_event()` targets PG `memory_events`, the store this
  same ticket retires).
- `context_builder.py` — wq#4553's builder, further rewired: PERMANENT DECISIONS/STANDING
  RULES/FRANK CORRECTIONS and FLEET FACTS now read minerva.db, not PG. No PG connection
  (`fm_db`) remains anywhere in the file.
- `freeze_pg_memory_tables.sql` — renames the 4 PG memory tables to `retired_*`.
  `recipes` untouched (harness/recipe_store.py:67 `PG_DBNAME="fleet_memory"`, live callers
  harness/fworker.py:55, dispatcher/foreman_dispatcher.py:92).
- `test_decisions_migration.py` — pinned in-memory-sqlite fixture: superseded_by
  derivation, and the render excluding a superseded row while including its successor.

## Scope correction found while staging (FLEET FACTS)

The design doc's section-ownership table only lists roster/episodes/decisions/hand-prose
— it omits `context_builder.py`'s FLEET FACTS section, which still read PG `fleet_facts`
(206 of 395 rows live, matching `source LIKE 'claude_memory.md%'`) after phase 1. Freezing
`fleet_facts` per the ticket's "revoke/rename the 4 PG memory tables" without migrating it
would have silently dropped that section on next regen. Migrated it the same way as
decisions (full table, not just the render's current filter — freezing is permanent, so
the mirror keeps everything PG had).

## REVOKE vs RENAME

The design allows either. Checked live (2026-10-23): `lumen` owns all 4 tables, and a
Postgres table owner's privileges bypass GRANT/REVOKE — revoking INSERT/UPDATE from the
owner enforces nothing. Used rename (to `retired_*`) instead, the one that actually blocks
a write while keeping the data as a live rollback alongside the pg_dump.

## Applied (2026-10-23)

Via `/home/lumen/bin/lumen-run` (wq#4570 precedent — this BUILD lane's sandbox has no
`--add-dir` into `/home/lumen/minerva`/`/home/lumen/backups`):

```
/home/lumen/bin/lumen-run start --name wq4554-apply -- bash /home/lumen/foreman/ops/wq4554-decisions-migration/install.sh
```

Result: `pg_dump` backup at `/home/lumen/backups/fleet_memory-pre-wq4288-20261023T095719Z.dump`;
87 PG `fleet_decisions` rows + 395 PG `fleet_facts` rows copied into minerva.db
(`{'pg_decisions': 87, 'pg_facts': 395, 'decisions_inserted': 87, 'facts_inserted': 395}`,
0 skipped); 482 `memory_audit` rows logged for lane `wq4554`; `claude_memory.md` regenerated
(47567 bytes, PERMANENT DECISIONS/FLEET FACTS/FLEET DEVICES all present); PG tables renamed
to `retired_fleet_facts`/`retired_fleet_devices`/`retired_agent_episodes`/
`retired_fleet_decisions`; `install.sh --verify` reports `VERIFY OK`.

Probe (b) overreach guard, run before and after the freeze:
`python3 -m pytest harness/tests/test_recipe_store.py` → 12 passed, 10 skipped both times —
the PG rename did not touch `recipes`.

## Not done here

Phase 3 (retire `memory-mcp.service`) is a separate ticket per the design doc's ordering.
