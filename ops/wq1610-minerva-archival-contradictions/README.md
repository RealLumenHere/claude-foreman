# wq#1610 — extend contradiction detection to archival_notes

## Root gap

Ref `/home/lumen/foreman/research/MINERVA_STALENESS_REVIEW.md` §2.4, §3.5, §3.6, §3.7
(fix #3). `lumen_dream.stale_prose_scan()` only ever reads `claude_memory.md`. The
episodic layer — `archival_notes`, 1686 rows, machine-retrieved into every
`lumen_episode.recall()`/precheck — has no invalidation happening on it at all: only
1 note is `superseded_by`-linked, 6 `revoked_at`, 5 merged out of 1686, and none of
that is driven by the bi-temporal fact store. A note can assert `nephthys.role =
ACTIVE` indefinitely after the store closed that fact and opened `DECOMMISSIONED`,
and every session that recalls the note gets the dead value with no flag on it.

## Scope (write rows only)

This ticket ships the detector and the table it writes. It does **not** touch
`lumen_recall2.search_v3`'s scoring — the soft contradiction penalty described in
§3.7 is wq#1614, explicitly gated on this script producing rows to penalize against.

## Schema — additive, no column on archival_notes

```sql
CREATE TABLE note_contradictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id TEXT NOT NULL,
    fact_id TEXT NOT NULL,
    dead_value TEXT NOT NULL,
    current_value TEXT,
    detected_at TEXT NOT NULL,
    cleared_at TEXT,
    UNIQUE(note_id, fact_id, dead_value)
);
```

wq#576 precedent: a side table keyed on the row it annotates (same shape
`note_merges` already uses for absorb/keep) rather than another `ALTER TABLE
archival_notes` — the hot table stays untouched. `ensure_schema()` creates it
idempotently on first run, same pattern as `lumen_state.init_schema()` /
`minerva_proposal_promoter.ensure_ticket_column()`.

## Detection

`note_contradiction_scan.py`:

1. **Dead-value index** (§3.5) over closed `state_facts` rows: a value qualifies
   only if `<=60` chars and matches `^[A-Za-z0-9._:/@-]+$` — no substring matching
   on narrative prose, which is exactly how §2.4 says the existing IP-only scanner
   earns its 2% coverage and how prose-matching would earn a noise reputation.
2. Scans `archival_notes` excluded the same way `search_v3` already excludes them
   unconditionally (`lumen_recall2.py`, wq#610): not `absorbed_id` in
   `note_merges`, `revoked_at IS NULL`, `superseded_by IS NULL`. A note nobody can
   retrieve doesn't need a contradiction row.
3. Two suppressors, both required on top of a per-line negation skip (mirrors
   `lumen_dream.NEGATION_RE`):
   - **changelog** — the fact's current value also appears anywhere in the note ->
     the note documents the transition, not asserts the dead value.
   - **as-of** — the note's `created_at` predates the fact's `valid_to` -> correct
     historical record at the time it was written.
4. Writes one `note_contradictions` row per `(note_id, fact_id, dead_value)`,
   idempotent via the unique constraint. A previously open row (`cleared_at IS
   NULL`) whose note was rescanned this run and no longer contains the dead value
   gets `cleared_at` set. Notes that became revoked/absorbed between runs are left
   alone — `search_v3` already drops them regardless of this table.

## Relationship to wq#1609 (fix #2, contradiction scanner generalisation)

The design doc lists this ticket as depending on wq#1609 generalising
`lumen_dream.superseded_ips()` into a shared dead-value index. At dispatch time
wq#1609 was still `claimed`/in-flight and this ticket's `deps` field was left
empty, so per standing autonomy instructions this was built as a self-contained
dead-value index scoped to `archival_notes` rather than parked waiting on a
sibling ticket. It implements the same §3.5 shape (structured-token gate,
changelog + as-of suppressors) independently of `lumen_dream.py`, so there is no
behavioral gap either lands in first. Once wq#1609 lands, extracting a shared
`dead_value_index()` helper both scripts call is a natural, low-risk follow-up —
not required for this ticket's acceptance criterion, which is that the change is
real and cited in a file this branch touches.

## Components

- `note_contradiction_scan.py` — the scanner (`ensure_schema`, `dead_value_index`,
  `live_notes`, `find_hits`, `scan`). Installed to `/home/lumen/bin`.
- `minerva-archival-contradictions.service` / `.timer` — daily
  (`05:00:00 UTC`), matching §3.5's "chased within a day, not six" cadence.
- `install.sh` — idempotent deploy (script + units + `enable --now`).

## Usage

```
minerva-archival-contradictions [--dry-run] [--limit N]
    [--db /home/lumen/minerva/minerva.db]
```

`--dry-run` reports counts without writing or clearing any row.

## Test procedure (scratch DB only, live minerva.db never touched)

```
python3 ops/wq1610-minerva-archival-contradictions/test_note_contradiction_scan.py
```

17/17 checks pass: structured-value hits get flagged (enum + IP shapes),
narrative-shaped closed values are never indexed, changelog/negation/as-of
suppressors each correctly withhold a row, revoked and absorbed notes are never
scanned, re-running is idempotent (no duplicate rows), fixing a flagged note's
content clears its row on the next scan, and `--dry-run` writes nothing.
