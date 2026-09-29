# wq#3148 — verify wq#3146 [minerva] sqlite leak ratchet RED: sweep_empty_source_proposals

Archetype 8 adversarial verify pass. Author: claude-opus-5-5. Verifier: claude-sonnet-5@high (fresh instance).

PR: http://100.121.17.5:3000/lumen/minerva/pulls/53 (merged to minerva `main` as 864fe16; PR branch tip 5f8bea0).

## Claim under test

`sweep_empty_source_proposals.py::main()` opened `minerva.db` and closed it only on the
two happy-path `return`s. Any exception mid-sweep (the `summary`-column `ALTER`, or a
locked `UPDATE`) left the connection open for the process lifetime — the wq#2895 lock
class. The fix wraps the connection in `contextlib.closing()` around a call to a new
`_sweep(conn, apply)` helper, so every exit path (return or raise) closes the connection.
Ratchet baseline is claimed unchanged.

## What I checked

- Cloned minerva fresh into `/tmp/fworker-wq3148/minerva`, fetched `refs/pull/53/head`
  (5f8bea0). Confirmed `main` already carries the squash-merge (864fe16, identical diff).
- Read the diff cold: `sweep_empty_source_proposals.py` (+14/-4) and new
  `tests/test_sweep_empty_source_proposals.py` (+83). No other files touched — in
  particular `tests/test_sqlite_leak_ratchet.py` and any baseline/allowlist file are
  untouched (`git diff main..pr-53 -- tests/test_sqlite_leak_ratchet.py` empty; no
  `*baseline*`/`*ratchet*` files in the PR's changed-file list). Baseline claim holds.
- Verified the actual close semantics by hand: `main()` now does
  `with closing(sqlite3.connect(a.db)) as conn: return _sweep(conn, a.apply)`. `closing()`
  calls `conn.close()` in `__exit__` unconditionally (return or exception), which is the
  correct fix for the bug class — `with conn:` alone (sqlite3's own context manager) only
  commits/rolls back and does NOT close, so a naive "wrap in `with conn:`" fix would NOT
  have worked; this PR used the right primitive.
- Traced the new exception-path test
  (`test_exception_mid_sweep_still_closes`): uses a fresh empty db file (no
  `classified_intel` table). `_ensure_summary_column()`'s `PRAGMA table_info` returns no
  rows for a missing table (no raise), so the actual raise is the subsequent
  `ALTER TABLE classified_intel ADD COLUMN summary TEXT` → `sqlite3.OperationalError: no
  such table`. Confirmed this is a real, reachable raise inside `_sweep`, not a
  test-only artifact.
- **Falsifiability probe (mandatory per doctrine)**: checked out the pre-fix commit
  (`864fe16~1`, i.e. origin/main before wq#3146) into a second worktree, copied only the
  new test file over (no other change), and ran it against the old `main()`:
  - `test_exception_mid_sweep_still_closes` → **FAILS** on pre-fix code:
    `assert [False] == [True]` — the connection is provably left open, exactly the bug
    claimed.
  - `test_dry_run_writes_nothing_and_closes` / `test_apply_rejects_empty_source_and_closes`
    → pass on both old and new code (dry-run/apply happy paths already closed correctly;
    only the exception path was broken).
  - Ran the actual ratchet (`tests/test_sqlite_leak_ratchet.py`) against the same pre-fix
    checkout: **fails** with
    `UNGUARDED::sweep_empty_source_proposals.py::main()::var=conn`, matching wq#3146's
    stated symptom verbatim.
- Ran the full new/ratchet suite on the PR's code: `tests/test_sweep_empty_source_proposals.py`
  + `tests/test_sqlite_leak_ratchet.py` → **7 passed**. Ratchet alone → 4 passed (clean,
  no new unguarded connections).
- Checked for callers/coupling on the new `_sweep(conn, apply)` split: only
  `sweep_empty_source_proposals.py` and its own test import it; one unrelated string
  match in `tests/test_minerva_promote.py` (a test name, not a call). Ran that file too:
  5 passed, no regression.
- Confirms the "baseline untouched" claim: PR is exactly 2 files, no ratchet/baseline
  file included.

## Verdict

**CONFIRMED.** The fix is correctly scoped, uses the right closing primitive (not the
common false-fix of `with conn:`), the new test suite genuinely exercises the previously
broken exception path (verified failing pre-fix, passing post-fix — satisfies the
falsifiable-probe requirement), the ratchet is clean post-fix and reproducibly red
pre-fix, and there is no evidence of scope creep, baseline tampering, or coupling
regressions elsewhere in the repo.

No contradiction found; not escalating to judgment.
