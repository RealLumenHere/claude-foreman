# wq#2907 — minerva.db: journal_mode=delete → WAL + busy_timeout + guard class

## Incident (2026-09-28, fleet-audit-2)

memoryd's leaked `BEGIN IMMEDIATE` (wq#2895, root cause fixed in minerva PR#41)
held minerva.db's write lock 04:36–05:11Z. Because the db ran
`journal_mode=delete`, that one transaction blocked **readers and writers**
fleet-wide for 35 minutes: lumen-embed-guard exit 2, lumen-ir-triage exit 1,
lumen-backup-critical "FAILED Minerva state store dump", wq / lumen_episode /
lumen_state all down. This package is the class fix for the symptom; wq#2895
fixed the specific leak.

In WAL mode readers never block on a writer, and writers queue on busy_timeout
instead of raising immediately.

## What is in this package

| file | role |
|---|---|
| `migrate_wal.py` | backup (sqlite3 online `.backup` API, never cp) → `PRAGMA journal_mode=WAL` → verify. Refuses on failed quick_check or failed backup; idempotent. |
| `wal_probe.py` | falsifiable concurrency probe (wq#2630): holds `BEGIN EXCLUSIVE` + INSERT in a child process, asserts a `mode=ro` reader still reads during the hold and a second writer waits `busy_timeout` then lands. **Exits 1 on a delete-mode db** (reader locked out — the incident behaviour) and when the hold outlasts the writer budget. |
| `test_wal_migration.py` | zero-quota pytest for both, including both failure legs of the probe. |

The guard-class fix for **lumen-ir-triage** is in this same branch at
`ops/wq2836-ir-triage/ir_triage.py`: `save()` retries a held lock with backoff
(`LOCK_BACKOFF_S`), sets `busy_timeout`, and `main()` converts a lock that
outlasts every retry into ONE deduped aq row
(`aq add ir-triage minerva-db-locked …`) + **exit 0** — the wq#2570/2571
lesson: a failed oneshot unit is restart/alert spam, the aq row is the alert.
The seen-set is not advanced, so the next timer fire retries the pass.

## Design checks done before migrating (ticket step 1)

Inventory of every opener (grep over /home/lumen/minerva, bin/, foreman/,
2026-09-28, lumen-job-wq2907-inv2):

- **No `immutable=1` and no read-only-filesystem opener exists.** Every
  read-only open uses a `file:…?mode=ro` URI (lumen_salience, prefs_block,
  minerva_verify, minerva_memory, ir_triage, retention_scorer, kernel queue
  reads, …) — WAL-compatible: `mode=ro` readers attach the existing -wal/-shm.
- `K.connect` = `/home/lumen/minerva/v4/memoryd/kernel.py:92-95`
  (`busy_timeout_s = 5.0`) — raise to ≥30 s in the minerva repo (follow-up).
- `backup-critical.sh:52` already uses `sqlite3 … ".backup"` (online API), so
  ticket step 4 needed **no cp→.backup rewrite**; the 09-28 dump failure was
  the CLI's default busy_timeout=0 hitting the held lock. Post-WAL, `.backup`
  is a reader and never blocks; adding `-cmd ".timeout 30000"` to the
  invocation is a cheap belt-and-braces follow-up.

## Runbook

```
# 1. migrate (backs up first; idempotent; safe with services up — the pragma
#    just waits on busy_timeout for a quiet moment):
lumen-run start --name wq2907-migrate -- python3 \
  /home/lumen/foreman/ops/wq2907-minerva-wal/migrate_wal.py

# 2. prove the incident shape is dead (60 s held write lock, live db):
lumen-run start --name wq2907-probe -- python3 \
  /home/lumen/foreman/ops/wq2907-minerva-wal/wal_probe.py \
  --db /home/lumen/minerva/minerva.db --hold-secs 60 --writer-timeout-ms 90000

# rollback (only if something regresses):
#   PRAGMA journal_mode=DELETE  (or restore the _prewal_ backup from
#   /home/lumen/backups/minerva_wal_migration/)
```

## Follow-ups (minerva repo / services — outside this repo, filed as a wq ticket)

1. `v4/memoryd/kernel.py:92` — `busy_timeout_s = 5.0 → 30.0`, and set
   `PRAGMA synchronous=NORMAL` in `K.connect` (synchronous is per-connection,
   NOT persisted by the migration; NORMAL is the recommended WAL pairing and
   loses no durability guarantees the fleet relies on — worst case on power
   loss is a rollback to the last commit, never corruption).
2. `memory_embed_guard.py:216-220` — the catch-all `except → sys.exit(2)` is
   the same guard class fixed here for ir-triage: catch `database is locked`,
   retry with backoff, then aq row + exit 0.
3. `lumen_state.py` / `lumen_episode.py` connects — `busy_timeout ≥ 30000`.
4. `backup-critical.sh:52` — `sqlite3 -cmd ".timeout 30000" …`.
