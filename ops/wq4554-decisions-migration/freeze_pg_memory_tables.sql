-- wq#4554 (wq#4539 phase 2, step 4): freeze the 4 PG memory tables now that
-- context_builder.py reads decisions and facts from minerva.db too (roster
-- and episodes already moved in phase 1, wq#4553). `recipes` is UNTOUCHED —
-- harness/recipe_store.py:67 (PG_DBNAME="fleet_memory"), imported by
-- harness/fworker.py:55 and dispatcher/foreman_dispatcher.py:92, both live
-- callers. `memory_events`/`memory_sources` are also untouched (out of this
-- design's 4-table scope).
--
-- REVOKE is not enough here: `lumen` OWNS all 4 tables (checked live,
-- 2026-10-23), and a Postgres table owner's privileges bypass GRANT/REVOKE,
-- so revoking INSERT/UPDATE from the owner changes nothing enforceable. The
-- design doc allows "revoke/rename" either way — rename is the one that
-- actually blocks a write. Renaming keeps the data as a live rollback
-- (alongside the pg_dump this ticket's install.sh takes first); nothing is
-- dropped.
--
-- wq#4578: `IF EXISTS` so the whole install.sh is re-runnable. Shipping a
-- fix to the renderer now means re-running this installer (the live
-- context_builder.py is untracked in minerva, so re-install IS the deploy
-- path), and a bare RENAME aborts the script under `set -e` once the tables
-- are already renamed — leaving the installer exited 1 before its own
-- --verify step. IF EXISTS makes a second run a no-op here instead.
ALTER TABLE IF EXISTS fleet_facts     RENAME TO retired_fleet_facts;
ALTER TABLE IF EXISTS fleet_devices   RENAME TO retired_fleet_devices;
ALTER TABLE IF EXISTS agent_episodes  RENAME TO retired_agent_episodes;
ALTER TABLE IF EXISTS fleet_decisions RENAME TO retired_fleet_decisions;
