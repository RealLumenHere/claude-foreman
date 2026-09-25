# wq#2555 — index_research.py final upsert_batch guard

Deliverable repo is `lumen/minerva` (ISSUE-LOG #272/#441/#687), not this
worktree's `foreman` checkout, so the acceptance-gate citation below points
at this report rather than the patched code directly — same cross-repo
citation gap and workaround as ISSUE-LOG #192 (wq#1440).

## What shipped

`lumen/minerva` PR #31, merged as commit `b8f8dbb33d` on branch
`feat/wq2555-index-research-final-upsert-guard` (minerva `main` now at
`6e1b9ae`), file `retrieval/index_research.py`:

- Mid-loop `upsert_batch` call (was `retrieval/index_research.py:206`,
  now the `try/except/finally` block at lines 205-217 post-merge): the
  `except Exception` previously never cleared `batch`, so a persistent
  DB-side failure (wq#2554's `UndefinedColumn` schema drift) re-sent and
  re-failed the same rows on every later `BATCH_SIZE` tick for the rest
  of the run. `batch.clear()` now runs unconditionally in `finally`.
- Final `upsert_batch(conn, batch)` (was line 216, no handler; now the
  `try/except/finally` block at lines 227-239 post-merge): previously
  raised uncaught on a DB error, skipping `conn.close()` and the wq#309
  `chunk_reembed` pass even though every embedding in the run was
  already paid for. Now logs via `log_error` per affected row and always
  falls through to `conn.close()` + `chunk_reembed`.
- `main()` returns `1` when `err_c` is nonzero (was always `0` on this
  path) so `lumen-research-index.timer` (oneshot, no `Restart=`) shows a
  failed run in `journalctl` instead of silently exiting clean.

## Verification

- `python3 -m py_compile retrieval/index_research.py` — passed pre-merge.
- Standalone regression test stubbing `upsert_batch` to raise
  (`RuntimeError("UndefinedColumn: schema drift")`, mirroring wq#2554)
  confirmed `batch.clear()` runs and `log_error` fires once per affected
  row — the shape both call sites share.
- No live DB reach from this build lane, so the exact Postgres
  `UndefinedColumn` path was not re-run end-to-end; the unit-level
  exercise of the handler logic is the available substitute.
