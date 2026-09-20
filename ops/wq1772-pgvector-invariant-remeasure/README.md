# wq#1772 — re-measuring the pgvector invariants

Read-only. No new code: every number comes from a script that already shipped. This file
exists so the next pass does not have to re-derive the command set, because these numbers
expire (ISSUE-LOG candidate #475) — I1 drifted 19 → 21 in the nine days between wq#1713's
pass and this one.

Results and their reading: `research/PGVECTOR_INVALIDATION_CONTRACTS.md` §8.1.

## The three measurements

`minerva-query` and `psql` are not on the build-worker Bash allowlist and
`/home/lumen/minerva/**` is outside the path sandbox, so everything goes through
`lumen-run` (ISSUE-LOG #320/#321). Each command prints `UNIT=…`; collect with
`lumen-run wait UNIT` then `lumen-run log UNIT`.

**(a) I1 and (b) I2** — wq#1776's shipped checker. `--raise-aq` is omitted deliberately:
without it the script persists nothing, which keeps the pass read-only. `--state` is still
pointed at `/tmp` as a second guard.

```bash
lumen-run start --name inv -- python3 \
  ops/wq1776-pgvector-chunk-doc-invariants/check_chunk_doc_invariants.py \
  --json --state /tmp/wq1772-nowrite-state.json
```

Returns `i1_stale_chunks`, `i1_docs_affected`, `i2_orphan_chunks`, and — answering (b)'s
second half — `doc_id_fk_cascades` with the full `pg_get_constraintdef` text.

Confirm the FK independently, since the whole orphan guarantee rests on it:

```bash
lumen-run start --name fk -- /home/lumen/bin/minerva-query sql \
  "SELECT conname, confdeltype, confupdtype FROM pg_constraint
   WHERE contype='f' AND conrelid='research_chunks'::regclass"
```

**Corpus totals** — note `position('foreman' in filepath) > 0` rather than `ILIKE
'%foreman%'`: a literal `%` in freeform SQL is read as a psycopg placeholder (#321).

```bash
lumen-run start --name corp -- /home/lumen/bin/minerva-query sql \
  "SELECT (SELECT count(*) FROM research_docs) AS docs,
          (SELECT count(*) FROM research_chunks) AS chunks,
          (SELECT sum(size_bytes) FROM research_docs) AS bytes,
          (SELECT min(mtime)::date FROM research_docs) AS oldest,
          (SELECT max(mtime)::date FROM research_docs) AS newest,
          (SELECT count(*) FROM research_docs
             WHERE mtime < now() - interval '90 days') AS older_90d,
          (SELECT count(*) FROM research_docs
             WHERE position('foreman' in filepath) > 0) AS foreman_docs"
```

**(c) Corpus reach** — wq#1713's script: `build_dead_value_index()` over `minerva.db`, the
§4.2 SQL prefilter, `scan_text()` on the survivors. Roughly three minutes at the current
corpus size, so give `wait` room.

```bash
lumen-run start --name reach -- python3 \
  ops/wq1713-pgvector-invalidation/measure_corpus_reach.py
lumen-run wait lumen-job-reach-<epoch> 400
```

**Cadence** — worth recording alongside the counts, since it says whether anything but a
research ticket would have noticed the drift:

```bash
lumen-run start --name tmr -- systemctl is-active \
  lumen-chunk-doc-invariants.timer lumen-contradiction-scan.timer lumen-research-index.timer
```

## Gotchas

- `state_facts` is **not** in pgvector. It lives in `minerva.db` (sqlite) and
  `minerva-query sql` cannot see it — you get a bare psycopg traceback. Read it through
  `cs.connect(cs.MINERVA_DB, readonly=True)` from a script under `lumen-run` instead
  (ISSUE-LOG candidate #474).
- `measure_corpus_reach.py:16` resolves wq#1609 from `/home/lumen/foreman`, the primary
  checkout — not from your worktree. If you are changing the scanner, that is the copy you
  are measuring against.
