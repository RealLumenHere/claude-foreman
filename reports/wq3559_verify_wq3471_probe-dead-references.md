# wq#3559 — verify-pass on wq#3471 / PR#1628

Archetype 8 adversarial verify. Author: claude-opus-5-5 (wq#3471). Verifier: claude-sonnet-5@high, fresh instance.

## Scope

wq#3471 claimed to fix the `probe-dead-references` false-positive flood (firing since
2026-09-16, 20-21 pages) by patching `tbl_sweep2.py` (the script
`/home/lumen/sentinel/probes/sentinel_probes.py:248` runs) for three bug classes:

1. `db_pat` didn't match a db path that starts right after an f-string substitution
   (`DB = f'{MINERVA}/minerva.db'`), so minerva tables got checked against `queue.db`
   instead (wq#1880 diagnosed this in the past; the fix never shipped).
2. Trigger grammar (`BEFORE UPDATE ON t`, `AFTER UPDATE OF col ON t`) got read as a table
   reference named `ON`/`OF`.
3. SQL keywords inside docstring prose (`"""an UPDATE that sets x..."""`) got read as real SQL.

PR#1628 (foreman commit `697b6d99`, squash-merged to main as `ac48689e`) adds
`ops/wq3471-tblsweep-false-positives/{README.md,install.sh,tbl_sweep2.py,tests/test_tbl_sweep2.py}`,
a tracked copy of the live `/home/lumen/audits/tools/tbl_sweep2.py` (that directory isn't a
git repo) with: `db_pat` extended to also anchor on `}`, `SKIP` += `{on, of}`, an
`ast`-based `docstring_lines()` skip, and `SCAN_DIRS` made overridable via `argv` so tests
can point the sweep at a fixture tree instead of the four live roots.

## What I checked

1. Read the diff cold (`git show ac48689e`, 4 new files, 170 lines).
2. Ran the shipped tests: `python3 -m pytest -q ops/wq3471-tblsweep-false-positives/tests` ->
   5 passed (f-string resolution, trigger grammar, docstring prose, a real-missing-table
   control, and an unparseable-file fallback).
3. Live liveness probe: `python3 /home/lumen/bin/sentinel-watch --check probe-dead-references --quiet`
   -> rc=0 (clear), and `--dry-run --only probes --json` does not list
   `probe/probe-dead-references` among current findings — consistent with the done-notes
   claim that the forced probe run passed and edge-cleared 2 aq rows.
4. Confirmed the two scripts PR#1628's `install.sh` depends on actually exist in-repo:
   `ops/wq2856-atomic-install/install-script` and `ops/_verify.sh`.
5. Cross-checked `/home/lumen/kb/ISSUE-LOG.md` row 1188 and `wq show 3471` — both match the
   PR's description of root cause and fix; no contradiction.
6. Could **not** independently verify the live-deployed `/home/lumen/audits/tools/tbl_sweep2.py`
   byte-matches the tracked copy (`install.sh --verify`), or re-run the sweep against the
   actual live scan roots (`/home/lumen/agent`, `/home/lumen/minerva`, `/home/lumen/messenger`,
   `/home/lumen/dashboard`) and recount 35->26 raw lines myself — none of those paths are in
   this worker's `--add-dir` grant. This is a sandbox limitation, not a contradiction; it's
   covered instead by checks #3 (clear in production) and #5 (ISSUE-LOG/ticket cross-check).

## Adversarial probes beyond the shipped tests

The ticket's own tests only exercise a *function* docstring and a same-directory f-string
resolve. I tried to break the fix on cases it doesn't cover, in `/tmp/fworker-wq3559/probe/`
against two real on-disk SQLite fixtures (`real.db` with table `real_t`, `wrong.db` with
`wrong_t`):

- **Module- and class-level docstrings** (the diff's `docstring_lines()` claims to cover
  `ast.Module`/`ast.ClassDef` too, not just `FunctionDef`): a file with a module docstring
  mentioning `ghost_t` and a class docstring mentioning `ghost_t` -> zero findings (correct
  suppression at both scope levels, not just the one shape the shipped test covers).
- **Control probe (must be able to fail)**: same fixture dir, a plain (non-docstring)
  `X = "SELECT x FROM ghost_t"` -> correctly fires `MISSING table 'ghost_t'`. This proves the
  docstring suppression above is a real, scoped effect and not the sweep silently going
  inert.
- **Trigger-ON/OF skip coexisting with a real miss in the same string**: 
  `"""CREATE TRIGGER a BEFORE UPDATE ON real_t BEGIN SELECT x FROM ghost_t; END;"""` ->
  fires exactly `MISSING table 'ghost_t'` once, and does not also flag `ON`/`real_t`. Confirms
  `tbl_pat.finditer` isn't short-circuited by the new `SKIP` entries and a genuine miss later
  in the same compound string still gets caught.

No case I constructed produced a false negative (a real missing table hidden) or a new false
positive. The fix is narrowly scoped to the three documented bug classes and doesn't appear
to weaken detection elsewhere.

## Verdict

**CONFIRM.** PR#1628 does what wq#3471 and the done-notes claim: shipped tests pass, the
live liveness probe is clear, dependencies it relies on exist, and my own adversarial probes
(module/class docstrings, a falsifiable control, and a compound trigger+miss string) found no
regressions or missed cases. The one thing I could not reproduce myself — the exact 35->26
raw-line count against the real live scan roots — is outside this worker's directory grant,
not a defect in the PR.
