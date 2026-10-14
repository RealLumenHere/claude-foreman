# wq#3847 verify: wq#3845 healer watch table drop of desktop-commander-mcp

Archetype 8 adversarial verify pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5
(fresh instance, same model as author).

Rework commits on `main`: `7efca7e0` (fix(healer): drop retired desktop-commander-mcp from
watch table — adds `ops/wq3845-healer-drop-desktop-commander-mcp/{install.sh,
healer_watch_table.diff, RUNBOOK.md}` + `ops/tests/test_wq3845_verify.py`) and `eaddbcb9`
(fix(healer): add post-deploy snapshot fixture for wq3845 evidence gate — adds
`healer_watch_table.yaml.deployed`). Root-apply done separately under wq#3846
(`reports/wq3846_root-apply_wq3845-healer-drop-desktop-commander-mcp.md`).

## What wq#3845 claimed

`lumen_self_healer.py`'s watch table (`/home/lumen/agent/healer_watch_table.yaml`, live,
outside this repo) carried a row for `desktop-commander-mcp`, a systemd unit that is now
`LoadState=masked` — retired per `docs/fleet/retired_services.yaml:319-331` (Commander
moved to Horus via `mcpw.lumennode.uk`). The wq#1112 stale-target guard flags this every
cycle. Fix: delete the row via an idempotent `install.sh`, verified read-only by
`install.sh --verify` (row absent, table still parses to a non-empty `units` map).
Rework note on the ticket claims PR#1838 closes a gap in the AC1 evidence gate by adding a
sha256-matched post-deploy snapshot proving the live file was actually touched, and that
`fworker.py verify-evidence` now passes clean.

## What I checked

- Read `install.sh`, `healer_watch_table.diff`, `RUNBOOK.md`, `ops/_verify.sh`, and
  `ops/tests/test_wq3845_verify.py` cold.
- Ran the bundle's own test suite standalone: `python3 -I ops/tests/test_wq3845_verify.py
  -v` → **5 passed** (`test_row_absent_passes`, `test_comment_mentioning_unit_is_not_a_row`,
  `test_row_present_fails`, `test_unparseable_table_fails`,
  `test_missing_table_is_error_not_drift`). This probe can fail: two cases
  (`test_row_present_fails`, `test_unparseable_table_fails`) assert a non-zero exit against
  deliberately-broken fixtures, and `test_missing_table_is_error_not_drift` exercises an
  input (absent file) outside the "row present/absent" population the fix targets,
  satisfying the wq#2630 falsifiability doctrine.
- Adversarially checked the deletion regex, `ROW='^[[:space:]]+desktop-commander-mcp:'`:
  anchored at line start, so a comment that merely *mentions* the unit name
  (`  # wq#3845: desktop-commander-mcp row removed, unit masked.`) cannot match — confirmed
  by the test, and by hand-tracing the regex against that exact line (a literal `#`
  immediately follows the leading whitespace, breaking the match before reaching
  `desktop-commander-mcp:`). Ruled out the failure mode where a future "soft delete" (row
  commented out, not removed) would silently pass `--verify`: it would not, since the
  commented line never matched $ROW against the same regex, and `--verify`'s own `_vmiss`
  check looks for the same literal row, not the unit name anywhere in the file.
- Independently re-ran the LIVE target myself (not just citing wq#3846's report):
  `lumen-run start --name wq3847-verify-live -- bash
  /home/lumen/foreman/ops/wq3845-healer-drop-desktop-commander-mcp/install.sh --verify` →
  `lumen-run status` = `DONE:0`; `lumen-run log` shows `VERIFY OK` / `LUMEN_JOB_EXIT=0`.
  Confirms the live `/home/lumen/agent/healer_watch_table.yaml` has no
  `desktop-commander-mcp` row and still parses to a non-empty `units` map, today
  (2026-10-14), not just at wq#3846's apply time.
- Verified the snapshot-fixture claim: `sha256sum
  ops/wq3845-healer-drop-desktop-commander-mcp/healer_watch_table.yaml.deployed` →
  `7c959ce545b373be00f5d3c8f3aae04019b357e0ac4ea3b1571e97738c7b25b9`, matching both the
  commit message (`eaddbcb9`) and `_runs/wq3845/evidence.json`'s AC1 citation verbatim — not
  just trusting the stated value.
- Cross-checked `harness/liveness.py:432-451` (`ops_verified`): runs `bash install.sh
  --verify` directly against the real live path (no env override), maps rc 0 →
  `SATISFIED`, rc 1 → `STILL_BLOCKED`, else `UNKNOWN`. Matches the ticket's stated liveness
  key `ops_verified(wq3845-healer-drop-desktop-commander-mcp)` and the contract in
  `ops/_verify.sh` (`vdone`'s exit-code mapping: 0 clean / 1 miss / 2 error) that
  `install.sh` implements.
- Checked `ops/_verify.sh`'s `vdone` against `install.sh`'s own `_V_ERR`/`_vmiss` usage:
  unreadable table → `_V_ERR=1` → exit 2 (not drift, an error) — matches
  `test_missing_table_is_error_not_drift`'s expectation of rc 2, not rc 1.
- Confirmed `eaddbcb9` and the equivalent commit now on `main` (`1b645639`) are
  byte-identical (`git diff eaddbcb9 1b645639` → empty) — the rework landed intact, no
  drift introduced by whatever merge/rebase path got it to `main`.

## What I did not find broken

- `install.sh`'s non-`--verify` path is genuinely idempotent: `grep -qE -- "$ROW" "$TABLE"`
  gates the `sed -i` delete, so a second run (e.g. from the duplicate wq#3816 bundle landing
  first) is a no-op printing `unchanged: $TABLE` — exactly what wq#3846's apply log shows
  happened.
- No scope creep: the four other `desktop-commander-mcp`-aware scripts the RUNBOOK
  disclaims (`commander-blocklist.sh`, `dc-redeploy.sh`, etc.) are untouched, correctly out
  of scope for a watch-table row removal.
- The evidence-gate rework (`eaddbcb9`) only adds a read fixture; it does not touch
  `install.sh`'s logic, so it carries no new risk to the deploy/verify contract itself.

## Verdict: CONFIRM

No contradiction found. The fix does what it claims, the acceptance probe can fail and
doesn't (drawn from both the in-population and out-of-population cases wq#2630 asks for),
and the live target — re-checked by me today, independently of wq#3846's own report — is
in the claimed state. Not re-implementing per ticket instruction; posting CONFIRM to the
board.

Refs: wq#3847, wq#3845, wq#3846.
