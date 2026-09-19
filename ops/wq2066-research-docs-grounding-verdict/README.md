# wq#2066 — research_docs.grounding_verdict

Split from wq#1695 item 3. The gate (`autonomy/fleet_grounding.py`) is live for new
reports; the existing corpus is not covered. Measured 2026-09-19 over the newest 400
`scratch/research` reports (`check_urls=False`): 65/400 (16%) fail on fleet claims that
do not resolve (`/opt/lumen/*`, invented hosts `lumen-1` / `lumen_net` /
`registry.lumen.local`, invented `lumen-*.service` units). Those reports still carry
valid web research, so nothing is quarantined: they are excluded from RAG instead.

## What ships here

| file | role |
|---|---|
| `grounding_verdict.py` | `parse_header()` (indexer), `migrate` (dump then additive DDL), `backfill` (runs `fleet_grounding.check` per doc), `status`, `RAG_FILTER_SQL` |
| `patch_index_research.py` | anchored, idempotent patcher that makes `index_research.py` write both columns from the `grounding:` header |
| `test_grounding_verdict.py` | 21 tests, fake connection + injected `check`, no live DB |
| `install.sh` | tests, then `install -m 0755` to `/home/lumen/bin/research-grounding-verdict` |

## Column semantics

- `grounding_verdict` — `'pass'` / `'fail'` / `NULL`. NULL = never assessed, and is
  **served** (a doc nobody checked is not a failing doc).
- `asserts_fleet_state` — the doc names at least one path / unit / identifier / table
  that does not resolve on the box. A `fail` from a format problem alone (too short,
  unterminated reasoning block) leaves it FALSE. This is the narrow reading of
  `research/PGVECTOR_INVALIDATION_CONTRACTS.md` §4.1 (`asserts_fleet_state`): it flags
  docs whose fleet claims are wrong today. §4.5.3 rendering can consume it later.

Backfill assesses with `check_urls=False` (no search context survives for old reports —
the mode the 65/400 measurement used) and passes the matching
`scratch/research_prompts_done/<stem>.txt` as `prompt_text`, so "given in the prompt"
exemptions apply as they did in that measurement.

## Deploy (order matters)

Steps 1, 3, 5 run from any session with DB creds. Steps 2 and 4 edit
`/home/lumen/minerva/**`, which is outside the build lane's write roots
(ISSUE-LOG #272, `fleet-exec-policy.yaml:67`); they were **not** done by the build
worker and are the open part of this ticket.

1. `research-grounding-verdict migrate --dump ~/backups/research_docs_pre_wq2066.csv`
   — writes an `id, filepath, content_hash, asserted_at` snapshot, refuses on an empty
   one, then `ADD COLUMN IF NOT EXISTS` x2, a CHECK, and a partial index on `'fail'`.
   Metadata-only in Postgres, no table rewrite, no re-embed.
2. **(minerva reach)** copy `grounding_verdict.py` next to `index_research.py`, then
   `python3 patch_index_research.py /home/lumen/minerva/retrieval/index_research.py`.
   Aborts untouched if any of the 5 anchors is missing or duplicated; writes
   `.bak.wq2066`. Order: 1 before 2, or the next daily index run fails on the INSERT.
3. `research-grounding-verdict backfill --dry-run` (prints counts, writes nothing;
   expect ~16% fail), then `backfill`. Resumable and NULL-only; `--force` re-assesses.
   Re-run it after each daily index: a re-indexed doc with no `grounding:` header is
   written NULL again and backfill re-covers it.
4. **(minerva / fleet reach)** in `minerva_verify`'s RAG-context query and the fleet
   `local_retrieval` node, add to the `research_docs` join / WHERE:
   `AND d.grounding_verdict IS DISTINCT FROM 'fail'` (`RAG_FILTER_SQL`). Do not write
   `<> 'fail'` — that drops the NULL rows. Combine with the wq#1776 `c.doc_hash =
   d.content_hash` join where a reader uses `research_chunks`.
5. `research-grounding-verdict status` — per-verdict counts; the `NULL` row should go
   to ~0 after backfill.

Rollback: the columns are additive and nothing else reads them until step 4, so
reverting step 4 restores prior retrieval; `ALTER TABLE research_docs DROP COLUMN` is
available but never required.
