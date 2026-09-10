# wq#1634 — audit row on `lumen_state.ingest` and on CLI `lumen_episode.commit`

MINERVA_PIPELINE_MAP.md §4.5, migration slot §5#7. Companion tickets: wq#1632
(the lifecycle gate this rides on), wq#1613 (verifier registry — consumes
`source_kind`).

**The code ships in the `minerva` repo, not here.** wq#1632 deferred the live
`lumen_state.py` edit because it could not read the file from its sandbox; this
lane opened a nested worktree of `/home/lumen/minerva` (ISSUE-LOG #275/#43) and
made the edit there. This directory is the paper trail.

Branch: `wq1634-minerva-audit-chokepoint` in `/home/lumen/minerva`.

## The gap it closes

`memory_audit` had 615 rows and **zero** with `table_name='state_facts'`:

```
$ sqlite3 -readonly /home/lumen/minerva/minerva.db \
    "SELECT count(*) FROM memory_audit WHERE table_name='state_facts'"
0
```

memoryd's kernel audits `archival_notes` writes that arrive through the daemon
(§1.6). Neither of the two write paths that skip the daemon was audited: `ingest()`
wrote fleet truth with no trail beyond a free-text `source_ref` (§1.1), and
`lumen_episode.py commit` from a CLI session bypassed both memoryd and the table
(§1.4) — and that fallback is the *primary* path today, because memoryd derives the
lane from a held wq claim (I-7) and a CLI shell holds none.

## What landed

| file | change |
|---|---|
| `minerva_audit.py` (new) | `audit()` / `ensure_table()` / `resolve_source_kind()` / `ticket_lane()`. `audit()` never commits — it writes on the caller's connection, so the audit row shares the transaction with the row it describes |
| `lumen_state.py` | one audit row per accepted ingest (`write` / `supersede` / `reconfirm`), plus one per sibling closed by a lifecycle decommission; `source_kind` stamped on the fact; `--stated-by-frank` / `--source-kind` |
| `lumen_episode.py` | `commit` POSTs to memoryd `/commit` when the daemon is healthy and the session is registered, else falls back to a direct write that carries its own audit row |
| `minerva_memory.py` | `write_note(..., audit=None)` — opt-in dict of audit fields, written inside the note's transaction. Default `None`, so every existing caller is byte-identical |
| `state_reconcile.py` | daily tailscale writes now declare `source_kind='observed'`, which is what lets wq#1613 supersede them on a probe mismatch |

### Schema

Additive only, self-healing in `lumen_state._migrate` (the pattern that file already
uses for `half_life_days` / `closed_by`):

- `state_facts.source_kind TEXT` — nullable, idempotent with wq#1613's own
  add-if-missing migration for the same column.
- `memory_audit` — `ensure_table()` creates it (with the append-only triggers) only
  on a DB that predates wq#575. On the live DB it is a `sqlite_master` read and a
  no-op, so no write lock is taken.

### `source_kind` vocabulary (wq#1607 §3.4)

Closed set, stamped at write time and never inferred afterwards:

| value | when |
|---|---|
| `frank_stated` | explicit `--stated-by-frank`. Only a human can assert it; wq#1613 never supersedes such a fact on a probe mismatch, so a value guessed later would silently disarm that guard |
| `observed` | probes and reconcilers (`--source-kind observed`) |
| `agent_written` | `$WQ_AGENT` is set |
| `unattributed` | no agent, no flag — a bare shell |

`frank_stated` and `observed` are wq#1613's anchors (`lumen_verify.py:67`, `:369`);
the other two split "a session wrote this" from "a shell wrote this", which is
exactly what `$WQ_AGENT` can answer and prose cannot.

### `lane` is read, not declared

From the ticket named by `source_ref` (`wq#1634` → `tasks.lane`), never from the
writer — memoryd's §2.4 rule, for its reason: a lane must not be able to spell its
own name. Fail-open to NULL if the queue DB is busy or the ref is free text; an
unattributed audit row beats an ingest that dies because a second DB was locked.

## Verification

`minerva/tests/test_minerva_audit_chokepoint.py` — 19 tests, throwaway DB per test
against the repo's real `state_registry.json`. Full minerva suite: 100 passed.

The acceptance criterion ("every write in a test session is answerable by SELECT on
`memory_audit`") was also run end-to-end against a `.backup` copy of the **live**
DB, so the migration and the audit path were exercised on the real schema and the
real append-only triggers:

```
{'action': 'superseded', 'subject': 'bes', 'predicate': 'tailscale_ip', ...}
{'action': 'reconfirmed', 'refreshed': True, ...}
{'action': 'insert', 'subject': 'bes', 'predicate': 'location', ...}

seq 616  supersede  state_facts  283814…  lane=build  agent_id=wq1634-a1  wq#1634
         detail: source_kind=agent_written bes.tailscale_ip=100.64.0.99 prior=49e5dc…
seq 617  reconfirm  state_facts  283814…  lane=build  agent_id=wq1634-a1  wq#1634
         detail: source_kind=agent_written bes.tailscale_ip=100.64.0.99
seq 618  write      state_facts  ce27f4…  lane=build  agent_id=wq1634-a1  wq#1634
         detail: source_kind=frank_stated bes.location=office closet

source_kind on facts: bes.tailscale_ip=agent_written  bes.location=frank_stated
```

Three writes in, three answerable rows out, `lane=build` read out of the real
`queue.db` rather than supplied. Audit seq continues from the live 615.

## Deploy — done

No install script and no unit: the change is in files the fleet already runs.
`lumen/minerva` PR #15 merged; `lumen-pr` fast-forwarded `/home/lumen/minerva` to
`04a962c`. Post-merge check against the deployed code and the live DB:

```
deployed lumen_state: /home/lumen/minerva/lumen_state.py
SOURCE_KINDS: ('frank_stated', 'observed', 'agent_written', 'unattributed')
ingest signature has stated_by_frank: True
live memory_audit rows: 615
live state_facts audit rows: 0
source_kind column present: True
```

The self-healing `ALTER TABLE state_facts ADD COLUMN source_kind` has already
applied cleanly on the live DB. `state_facts` audit rows are still 0 because no
ingest has run since the merge — the first genuine write produces the first row.
**Nothing was written to the live store to manufacture that number**: reconfirming
a fact would bump `last_confirmed_at`, which asserts evidence this lane does not
have, and inventing a subject/predicate to demo with would put test junk in fleet
truth. The behaviour is demonstrated above on a `.backup` copy instead.

The query the ticket exists to make possible:

```sql
SELECT ts, action, row_id, lane, agent_id, source_ref, detail
FROM memory_audit WHERE table_name='state_facts' ORDER BY seq DESC LIMIT 20;
```

## What this deliberately does not do

- **No `memory_audit` schema change.** `source_kind` rides in `detail` on the audit
  row and in its own column on `state_facts`. Widening an append-only,
  trigger-guarded table under a live daemon is a migration, not a field addition,
  and the audit row's job here is provenance rather than query surface.
- **No second audit row when memoryd accepts a commit.** The kernel already wrote
  one inside its own transaction; a duplicate would double-count the write in
  exactly the query this ticket exists to make trustworthy.
- **No `archival/*.md` mirror on the memoryd path.** `write_note` also drops a
  frontmatter markdown file; the kernel does not, so a commit memoryd accepts gets
  the DB row and the audit row but no file. That is already true of every lane
  commit today and nothing in minerva reads those files (`ARCHIVAL_DIR` has exactly
  one user, `minerva_memory.write_note`), so this extends existing behavior rather
  than introducing a new class of it. Restoring the mirror would mean splitting the
  file-write out of `write_note` — a refactor of a widely-called function for a
  write-only artifact, which belongs in its own ticket.
- **No audit on `write_note`'s other callers.** `audit=` is opt-in, so convo-learn,
  continual-learn and `seed()` are unchanged. Bringing them in is a separate
  decision about what "a note write" means for a 6-hourly batch job.
- **`lumen_state_single_owner_migrate.py` still writes raw SQL.** It is a one-shot
  migration, not a runtime writer. Every runtime writer — including
  `state_reconcile.py` and `backfill_state.py` — goes through `ingest()` and is now
  audited by construction.
