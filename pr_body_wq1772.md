## What

Re-measures the pgvector chunk/doc invariants I1/I2 and the corpus reach of the dead-value
index, read-only, against live stores on 2026-09-20. Results land in
`research/PGVECTOR_INVALIDATION_CONTRACTS.md` §8.1, alongside the reproducible command set
and two staged ISSUE-LOG rows. No code changes.

| | 2026-09-11 | 2026-09-20 |
|---|---|---|
| I1 stale chunks | 19 | **21** (21 docs) |
| I2 orphan chunks | 0 | 0 |
| FK | `confdeltype='c'` | `ON DELETE CASCADE`, confirmed twice |
| docs · chunks | 10,566 · 19,771 | 11,464 · 21,552 |
| index · candidates · elapsed | 64 · 2,862 · 110.8s | 87 · 3,617 · 168.0s |
| hits · true positives | 5 action · 0 | 9 action · **0** |

## Why

The ticket carried a 2026-09-11 note saying a previous pass had already answered it and it
should simply be closed. Every number that note cited turned out to be drifting state, so
re-running was the honest close. Five minutes of read-only work, two findings a
close-on-note would have buried:

**I1 is a slow leak, not a one-off window** — +2 in nine days against 8.5% corpus growth.
The checker from the enforcement ticket is installed
(`/home/lumen/bin/check-pgvector-invariants`, 2026-09-18) but ships no timer by design, and
`lumen-chunk-doc-invariants.timer` is `inactive`, so nine days of drift went unnoticed until
this pass counted by hand. Cadence wiring filed as **wq#2152** (deps wq#1788).

**A precision fix landed, and exposed a worse class beneath it.** All four
hyphenated-compound false positives from 09-11 are gone. Eight of the nine new hits are a
single new class: `isis.service_state` closed `UP → DEGRADED`, and `UP` is two characters,
so `self_identifying()` correctly declines to treat it as self-identifying and scopes it —
but `boundary_re()` compiles case-insensitively and the scope test only needs the subject
token and a predicate cue somewhere in the *enclosing section*, so a word-boundary match on
`UP` hits the English preposition: *"Spin up a FastAPI orchestrator"*, *"Set up the Docker
container"*. Not a recurrence of the earlier fix — there the value was wrongly unscoped;
here it is correctly scoped and still wrong, because subject-plus-cue is far too weak a
scope for a value that is also a function word. Filed as **wq#2151**.

§4.2's verdict is unchanged and now has two independent passes behind it: **0 true
positives**, twice, nine days and 898 documents apart. The blocker is precision, not reach —
a third of the corpus already reaches the matcher.

§8.1 is written as a *dated* section with §8's 09-11 account left standing, per that
document's own §2.3 argument that a dated record is not wrong.

## How

No new measurement code: every number comes from a script that already shipped, run through
`lumen-run` because `minerva-query`/`psql` are off the build-worker Bash allowlist and
`/home/lumen/minerva/**` is outside the path sandbox.

- I1/I2 and the FK: the enforcement ticket's `check_chunk_doc_invariants.py --json`, which
  reports `i1_stale_chunks`, `i1_docs_affected`, `i2_orphan_chunks` and the full
  `pg_get_constraintdef` text. FK re-confirmed independently via `pg_constraint.confdeltype`.
- Corpus reach: the prior research ticket's `measure_corpus_reach.py` —
  `build_dead_value_index()` over `minerva.db`, the §4.2 SQL prefilter, `scan_text()` on the
  survivors.
- Corpus totals and timer state: two `minerva-query` selects.

Full command set, including the read-only flags and the `%`-as-placeholder workaround, in
`ops/wq1772-pgvector-invariant-remeasure/README.md`.

## Testing

Measurement-only change; the scripts it drives carry their own suites and are untouched.
Evidence is the live run itself:

- `check_chunk_doc_invariants.py --json` → `i1_stale_chunks: 21`, `i1_docs_affected: 21`,
  `i2_orphan_chunks: 0`, `doc_id_fk_cascades: true`, `FOREIGN KEY (doc_id) REFERENCES
  research_docs(id) ON DELETE CASCADE`.
- `pg_constraint` → `research_chunks_doc_id_fkey · c · a`, agreeing with the above by a
  second route.
- `measure_corpus_reach.py` → 87 index entries, 3,617 candidates, 168.0s, 9 action / 0 info.
- Each hit's `excerpt` is the matched line itself
  (`lumen_contradiction_scan.py:316`), so the "0 true positives" reading is read off the
  printed lines rather than inferred — all eight `UP` hits show the preposition in ordinary
  prose, and the ninth is the same hypothetical failure-mode table flagged on 09-11.

## Risk / rollback

None. Read-only throughout: `check_chunk_doc_invariants.py` was run without `--raise-aq`, so
it persists nothing, with `--state` pointed at `/tmp` as a second guard; `minerva-query`
enforces its own `SELECT`/`WITH` gate under `default_transaction_read_only = on`. The diff
is three documentation files and no executable code, so rollback is a revert with no
operational effect.

## Also here

`ops/wq1772-pgvector-invariant-remeasure/ISSUE-LOG-CANDIDATE.md` stages rows 474/475 for
`kb/ISSUE-LOG.md`, which is guard.sh read-only for workers (refusal reproduced this run).
474: `minerva-query sql` cannot see `state_facts`, which lives in sqlite rather than
pgvector, and fails with a bare psycopg traceback. 475: when an "already done" note cites
counts, re-measure before closing — a measurement ticket's answer expires, an implementation
ticket's does not.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
