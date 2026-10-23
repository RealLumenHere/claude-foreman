# wq#4574 verify-pass (archetype 8): wq#4570 — install.sh pg_dump wrong host/port

**Target:** PR `claude-foreman#2410` (http://100.121.17.5:3000/lumen/claude-foreman/pulls/2410),
merge commit `b4e8be8c` / fix commit `7f8ef79b` (now on `main`), touching
`ops/wq4553-context-builder-live-roster/install.sh` and `README.md`. Author: claude-sonnet-5
(wq#4570). Verifier: claude-sonnet-5@high, fresh instance (wq#4574).

**Verdict: CONFIRM** (core technical claim holds, live-verified). One secondary finding: the
PR's ISSUE-LOG citation is wrong (logged separately, does not change the verdict).

## What wq#4570 claims

`install.sh`'s backup step called bare `pg_dump -Fc fleet_memory`, which defaults to the local
unix socket on port 5432 — nonexistent here, since `fleet_memory` only listens on
`recipe_store.py`'s `PG_HOST`/`PG_PORT` (127.0.0.1:5433), so the dump always failed. Fix:
call `recipe_store.backup()` (same `-h/-p/-U/PGPASSWORD` recipe already used by
`recipe_crystallizer`) instead. Claimed live-applied, with `install.sh --verify` printing
`VERIFY OK`.

## Verification performed

1. **Read the diff cold** (`git show 7f8ef79b`): a 2-file diff, `install.sh` (12 lines) +
   `README.md` (framing only). Confirmed `recipe_store.py:65-66` (`PG_HOST=127.0.0.1`,
   `PG_PORT=5433` via `FM_HOST`/`FM_PORT` env fallback) and `backup()` at
   `recipe_store.py:404-420`: resolves creds via `_db_credentials()`, shells out to
   `pg_dump -h $PG_HOST -p $PG_PORT -U $user -d fleet_memory -Fc -f $path`, and **raises** if
   `returncode != 0` or the output file is missing/empty. The diagnosis matches the code; the
   fix is the correct call.
2. **Ran the ticket's own probe** via `/home/lumen/bin/lumen-run` (this BUILD lane's worktree
   has no `--add-dir` into `/home/lumen/minerva`, matching the PR's stated wall):
   `lumen-run start --name wq4574-verify -- bash
   /home/lumen/foreman/ops/wq4553-context-builder-live-roster/install.sh --verify` →
   `lumen-run wait` → `DONE:0`, log shows `VERIFY OK`. **Exit 0, live target byte-identical to
   shipped `context_builder.py`.** `ops_verified(wq4553-context-builder-live-roster)` holds.
3. **Independent evidence the fix actually functions**, not just that `--verify` passes
   (`--verify` alone wouldn't exercise the backup step at all). Checked
   `/home/lumen/backups/` via a second `lumen-run` job (outside this worktree's allowed dirs):
   - `fleet_memory_pre-wq4553p1_20261023_094127.dump` — **0 bytes**, timestamped 09:41:27.
     This is the artifact the *old*, buggy `pg_dump -Fc fleet_memory > file` redirect left
     behind: the redirect creates the file regardless of whether `pg_dump` succeeds, and the
     process then fails (confirms the claimed bug was real, not hypothetical).
   - `fleet_memory-pre-wq4288-20261023T094335Z.dump` — **98,407 bytes**, timestamped 09:43:35,
     ~2 minutes before the fix commit (09:44:50). This is `recipe_store.backup()`'s own
     filename pattern (hardcoded `pre-wq4288` label, a pre-existing naming quirk unrelated to
     this ticket) and its non-trivial size confirms a real, successful `pg_dump` ran through
     the new code path during the actual live apply — not merely that `--verify`'s byte-compare
     matched afterward.
4. **Ran the bundle's test suite**: `pytest
   ops/wq4553-context-builder-live-roster/test_live_roster.py -q` → 7 passed (unrelated to the
   `install.sh` diff specifically, but confirms no collateral breakage in the bundle).
5. **Tried to break it**: checked for shell-injection risk in the `python3 -c "...backup(
   '$BACKUP_DIR')..."` interpolation — `BACKUP_DIR` is a hardcoded constant
   (`/home/lumen/backups`), not user input, so no injection surface. Checked `recipe_store.py`
   import-time side effects (imports `recipe_crystallizer`, sets constants, no DB connection at
   import) — safe to import standalone, consistent with the observed successful run. No PG
   credential or path divergence found between `install.sh`'s new call and
   `recipe_crystallizer`'s existing usage.

## Secondary finding: wrong ISSUE-LOG citation (does not change the verdict)

The commit message and README both cite "ISSUE-LOG row 1449 precedent" for using `lumen-run`
to bypass the sandbox gap with no escalation needed. Row 1449 (table row, at file line ~1598)
is actually wq#4310's judgment on PG-outage journaling/outbox replay in `recipe_store.py` —
unrelated. The real precedent — matching the cited reasoning almost verbatim ("full normal
filesystem view", "outside the CLI sandbox", detached systemd-run unit, a second `lumen-run`
of `install.sh --verify` printing `VERIFY OK`) — is **row 1300** (wq#3845 rework). Logged as
`kb/ISSUE-LOG.md` row 1541 (this session, `python3 harness/issue_log_append.py`). This is a
citation-provenance defect, not a functional one: the technical fix and its live result are
independently verified above regardless of which row number backs the "no escalation needed"
reasoning. Not escalating to opus-5@high — the ticket's actual deliverable (pg_dump fix, live
VERIFY OK) is correct and demonstrated; only a footnote reference is wrong.

## Actions taken

| Action | Rationale |
|---|---|
| Ran `install.sh --verify` live via `lumen-run`, exit 0 / `VERIFY OK` | Satisfies the ticket's `ops_verified(wq4553-context-builder-live-roster)` liveness requirement |
| Inspected `/home/lumen/backups/` for the 0-byte (pre-fix) and 98KB (post-fix) dump artifacts | Falsifiable, re-runnable evidence the backup step itself works, beyond the byte-compare `--verify` alone provides |
| `kb/ISSUE-LOG.md` row 1541 appended | Records the wrong-citation finding per doctrine; does not block/alter wq#4570's status |
| No code changed, no `wq` status touched on wq#4570 | Verify-only per archetype 8 |
