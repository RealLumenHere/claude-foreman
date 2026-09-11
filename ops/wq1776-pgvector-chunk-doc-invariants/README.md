# wq#1776 — enforce and alarm the pgvector chunk/doc coherence invariants

## Root gap

`research/PGVECTOR_INVALIDATION_CONTRACTS.md` §4.7 (ranked fix #4) names two
invariants over `research_docs`/`research_chunks` that nothing checked:

- **I1** — every chunk's `doc_hash` must equal its parent doc's current
  `content_hash`. A mismatch is a chunk cut from a version of the file that no
  longer exists — served text attributed to a document that now says
  something else.
- **I2** — every chunk's `doc_id` must resolve to a live `research_docs` row.
  An orphan is a chunk left behind by a deleted document.

## Measured live, 2026-09-11 (read-only, via `lumen-run` + `minerva-query`
since neither is on the build-worker Bash allowlist — ISSUE-LOG #321)

```
total docs: 10,566   total chunks: 19,771
I1 stale chunks : 19   (19 distinct docs affected)
I2 orphan chunks: 0
research_chunks_doc_id_fkey: FOREIGN KEY (doc_id) REFERENCES research_docs(id) ON DELETE CASCADE
```

Two things the design doc left open, now resolved with a query instead of a
guess:

1. **I2 is already 0, and already structural, not conventional.**
   `research_chunks_doc_id_fkey` already carries `ON DELETE CASCADE`
   (confirmed via `pg_constraint`, not assumed). §4.7(b)'s proposed migration
   is unnecessary — there is nothing to add. `check_chunk_doc_invariants.py`
   re-verifies this fact on every run rather than trusting it once, and
   alarms if the constraint is ever dropped or altered (§"Alarming" below).
2. **I1 is not zero.** 19 chunks across 19 documents are currently being
   served from a stale cut — real, present drift, not a hypothetical. This is
   `federated_chunks`-shaped: the design doc's proof-by-existence
   (`indexed_at=2026-08-16` against a rewritten source) has a sibling in the
   main corpus, just two orders of magnitude smaller (19 of 19,771 vs. the
   whole `federated_chunks` table).

## What this ships

`check_chunk_doc_invariants.py` — a standalone, read-only reporting +
alarming tool (mirrors the `ops/wq1609-minerva-contradiction-scan/` and
`ops/wq1613-minerva-verifier-registry/` precedent: connects directly with
`psycopg2` using the same `LUMEN_PGVECTOR_*` / `/home/lumen/agent/.env`
convention as `index_research.py` and `/home/lumen/bin/minerva-query`, forces
`SET default_transaction_read_only = on`, never writes to `research_docs` or
`research_chunks`).

```
python3 check_chunk_doc_invariants.py              # human report, exit 0
python3 check_chunk_doc_invariants.py --json
python3 check_chunk_doc_invariants.py --raise-aq    # + persist state, alarm on regression
```

Sample output (live, see above for the numbers):
```
pgvector chunk/doc invariants: I1 stale-chunks=19 (docs affected=19) · I2 orphan-chunks=0 · doc_id FK cascades=True
```

Both counts print on every run whether or not anything is wrong — per
wq#1611, "a zero nobody prints is indistinguishable from a check that never
ran."

### Alarming

`--raise-aq` persists the last-seen counts to a JSON state file (default:
`state.json` next to the script; override with `--state PATH` or
`PGVECTOR_INVARIANTS_STATE`) and raises one `aq add pgvector <key> ...` row,
per calendar day, per regression:

- I1 or I2 grew since the last recorded run.
- the `doc_id` FK's `ON DELETE CASCADE` disappears (i.e. someone alters the
  constraint later and I2's structural guarantee quietly reverts to
  conventional).

`aq` dedups on `(source, key)`, so re-running the same day is a no-op — same
pattern as `lumen_coverage.py`'s week-over-week regression check (wq#1611). A
decrease, or the first run with no prior state, raises nothing.

## Contract, both directions (§4.7)

- **Readers** must join `research_chunks` to `research_docs` **with**
  `AND c.doc_hash = d.content_hash`. A chunk that fails the join is text that
  no longer exists in the file and must not be served; if every chunk for a
  doc fails, the caller falls back to doc-level retrieval (the pre-chunking
  behaviour, already safe). **Not shipped here**: the retrieval code that
  would need this join lives at `/home/lumen/minerva/retrieval/` (per
  `research/MINERVA_PIPELINE_MAP.md:110` and the `lowres_chunking` staging
  report), and `/home/lumen/minerva/**` is outside every build worker's
  `--add-dir` set by deliberate policy (`fleet-exec-policy.yaml:67`, wq#1467,
  ISSUE-LOG #272) — the file is neither readable nor writable from this
  worktree. Same wall wq#1609 hit for `lumen_dream.py`; same resolution:
  ship the enforceable half, document the rest as a deploy step.

  **Deploy step (needs a session with `/home/lumen/minerva` reach):** in
  every reader that does `... FROM research_chunks c JOIN research_docs d ON
  c.doc_id = d.id`, add `AND c.doc_hash = d.content_hash` to the join
  condition. `check_chunk_doc_invariants.py`'s `INVARIANT_SQL` is the query to
  paste into that session to re-find the current call sites and re-measure
  I1 after the patch — it should still report the same drift count until the
  underlying `chunk_reembed` pass catches those 19 docs up, which this
  ticket does not force.

- **`chunk_reembed`** — the design doc assumed it deletes old chunk rows for a
  `doc_id` before writing new ones. This session could not read
  `/home/lumen/minerva/retrieval/chunk_reembed.py` to confirm that (same
  sandbox wall as above); `agents/work-queue/reports/aq447_embed_drift_investigation.md:58-60`
  describes it as `INSERT ... ON CONFLICT (filepath, chunk_ix) DO UPDATE`
  (confirmed live: `research_chunks` carries a
  `research_chunks_filepath_chunk_ix_key` UNIQUE constraint), which is an
  upsert, not a delete-then-insert. An upsert alone would NOT clean up excess
  high-`chunk_ix` rows left behind when a document shrinks on re-chunk — but
  I1 already catches that case regardless of which write pattern
  `chunk_reembed` uses, because a leftover row still carries the *old*
  `doc_hash`, which by then differs from the doc's current `content_hash`.
  The 19 live I1 hits are consistent with this. Worth a
  from-`/home/lumen/minerva` read to confirm directly (this worktree's
  `/home/lumen/kb` is guarded read-only for workers — wq#1719/wq#900 — so it
  is recorded here rather than as an ISSUE-LOG entry); not blocking this
  ticket since the invariant enforced here (I1, checked at read time) holds
  regardless of which write pattern produced the drift.
- **Both counts reported by the daily scan and by `handoff verify`**
  (`/home/lumen/bin/handoff`, `verify()`) — not wired in by this ticket.
  `/home/lumen/bin` is reachable from this worktree, but it is a separate,
  actively-shared git repo (other concurrent build sessions had uncommitted
  changes in it at the time of this ticket — `slock`, `wq` modified, several
  untracked installs) and `handoff` is a tracked file on its `main`; editing
  it live risked colliding with those sessions. **Deploy step**: add a
  `## pgvector chunk/doc invariants` section to `verify()` calling
  `python3 /home/lumen/bin/check-pgvector-invariants` (after `install.sh`
  below), same shape as the existing `## fact coverage` section
  (`handoff:44-50`). Folding into `lumen-contradiction-scan.timer`'s daily
  cadence (§4.3) is ranked fix #3, a separate ticket (`--corpus` tier) — this
  ticket only ships the check itself and the `--raise-aq` alarm path; wiring
  a *schedule* around it is that ticket's job or a follow-up install step.

## Install

```
ops/wq1776-pgvector-chunk-doc-invariants/install.sh
```

Installs `check_chunk_doc_invariants.py` to
`/home/lumen/bin/check-pgvector-invariants` and runs one live report. Does
not install a timer (see "both counts reported..." above) — wire it into
`handoff verify` and/or the corpus tier of `lumen-contradiction-scan` per the
deploy steps above once those land.

## Tests

`python3 -m pytest -q test_check_chunk_doc_invariants.py` — 14 tests, all
green: invariant-counting from a fake cursor (clean / drifted / no-FK), the
report line, alarm-on-growth for I1/I2/cascade-loss, no-alarm on decrease or
first run, `aq` failures swallowed (never crash the caller), state
load/save/corrupt-file handling. No live DB or live `aq` calls — the one live
verification is the "Measured live" section above, run once by hand via
`lumen-run` (ISSUE-LOG #321) and cross-checked against `check_chunk_doc_invariants.py --json`'s own output.
