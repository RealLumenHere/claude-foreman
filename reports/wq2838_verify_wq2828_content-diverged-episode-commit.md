# wq#2838 — verify-pass on wq#2828 [repo-sweep] minerva CONTENT_DIVERGED

Verifier: claude-sonnet-5@high (fresh instance). Author: claude-opus-5-5.
Target: http://100.121.17.5:3000/lumen/minerva/pulls/40, landed as 8a32a32 on
/home/lumen/minerva main. Verdict: **CONFIRM**.

## What was shipped

wq#2828 found /home/lumen/minerva main 1 commit ahead of origin (133399a, an
unpushed commit from wq#2772 that closed without a PR). It pushed that same
commit on a branch, opened PR #40, squash-landed it as 8a32a32, and re-pointed
local main to origin. `repo_reconcile.py status` now reports `IN_SYNC` (verified
live, this session). The commit itself is wq#2772's real fix: `lumen_episode.py`
+ `minerva_memory.py` make episode commit DB-first (archival row + audit row +
recall_fts event in one transaction, committed, read back from a fresh ro
connection, `NotPersisted` raised if anything is missing) before the `.md`
mirror and the embedding, reversing the old order that caused 26 silently
orphaned episodes (.md on disk, no DB row) when a SIGKILL landed mid-embed.

## Adversarial checks run

- Read `lumen_episode.py` and `minerva_memory.py` diffs in full (not just the
  hunks) via `git show 8a32a32:<path>` against the pre-image, including
  `write_note_verified`, `verify_landed`, `embed_note`, `_write_md`, and the
  CLI `_main()` exit-3 path.
- Ran the new/updated suite: `pytest /home/lumen/minerva/tests/test_minerva_audit_chokepoint.py`
  — 24/24 pass, including the two adversarial ones that most matter:
  `test_wq2772_embed_death_cannot_orphan_the_note` (mocks `embed()` to raise
  `KeyboardInterrupt` mid-commit; row + FTS event still land, only the
  embedding is NULL) and `test_wq2772_missing_row_raises_and_writes_no_md`.
- Ran the full repo suite: `pytest /home/lumen/minerva/tests/` — 170/170 pass.
- Checked prod `/home/lumen/minerva/minerva.db` for pytest bleed-through
  (wq#2784's earlier failure mode) by title-matching the literal strings the
  new tests use (`killed mid embed`, `ghost write`, `dup commit`, etc.) — zero
  hits, tests are isolated via the `db`/`episode` fixtures' `MINERVA_DB`
  monkeypatch.
- Re-ran wq#2772's own acceptance proof live against the real DB:
  `lumen_episode.recall('BLUEFOX', top_k=5)` still ranks both phone notes
  1st/2nd — the exact case that was silently lost before this fix.
- Traced the `direct_write = fell_back is not None` embed-gating in `commit()`
  against the pre-image: old code only ever embedded on the fallback path too
  (via `mm.write_note`'s inline embed call; the memoryd-accept path never
  called `write_note` at all), so the new gating is behavior-preserving, not a
  new asymmetry — confirmed by reading both versions side by side rather than
  assuming from the diff alone.
- Checked ON CONFLICT semantics (`note_id` is a content hash, repeat commits
  UPSERT) aren't broken by the new code path: embedding column excluded from
  the `DO UPDATE SET` list, so a repeat commit under a dead embedder keeps the
  prior vector — matches `test_wq2772_repeat_commit_keeps_provenance_and_embedding`.
- Looked for a read-after-write race between the writer connection (rw,
  default rollback-journal mode, no WAL pragma) and `verify_landed`'s fresh
  `mode=ro` connection: writer closes after commit before the ro connection
  opens, sequential single-threaded control flow — no race.

No contradiction found. No escalation needed.

## Evidence

- `python3 -m pytest /home/lumen/minerva/tests/ -q` → `170 passed in 25.99s`
- `python3 /home/lumen/foreman/harness/repo_reconcile.py status --repo /home/lumen/minerva`
  → `IN_SYNC ... main == origin/main at 8a32a32. Nothing to do.`
- `lumen_episode.recall('BLUEFOX')` → both BLUEFOX NX1 notes returned, ranked
  top 2, live against prod `/home/lumen/minerva/minerva.db`.
