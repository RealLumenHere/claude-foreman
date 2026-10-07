# wq#3509 — cross-repo evidence note (same shape as wq#2569/wq#2573)

The dispatcher checked this ticket out as a **foreman** worktree, but the code
half of the deliverable (`unit-drift` digest dedup) lives in the **lumen-bin**
repo. Applied the wq#192/wq#2569/wq#2573 workaround: nested a real bin
worktree under this worktree's gitignored `_probe/` prefix, built + tested the
fix there, landed it via `lumen-pr` from inside that nested tree, then removed
the nested worktree. This note is the pointer so a citation can resolve
against *this* branch's diff for AC1 while the real bin-side change is
documented here.

## lumen-bin change (this ticket's code, not part of this branch's diff)

- PR: http://100.121.17.5:3000/lumen/lumen-bin/pulls/233 — branch
  `fix/wq3509-digest-hash-dedup`, commit `3ac0a8e`.
- `unit-drift`: `check --digest` now hashes the full change set and suppresses
  the ntfy while the hash matches the last-notified one
  (`~/.cache/unit-drift-digest.state`, `UNIT_DRIFT_DIGEST_STATE` test
  override); hash recorded only on `ntfy-send` exit 0 (failed send retries);
  CLEAN clears the state alongside the wq#3466 row auto-resolve; ntfy body
  carries the 12-char hash prefix.
- Tests: `tests/test_wq3509_unit_drift_digest_dedup.py` (5 new) +
  `tests/test_wq3466_unit_drift_aq_key.py` (4 existing, body-format assertion
  + tmp `DIGEST_STATE` updated) — 9 passed via
  `python3 -m pytest tests/test_wq3466_unit_drift_aq_key.py
  tests/test_wq3509_unit_drift_digest_dedup.py -q` inside the nested worktree.

## foreman repo change (this branch's own diff)

- `design/wq3509_unit_drift_alert_lifecycle.md` — the design doc the ticket
  asks for first: what wq#3466 already fixed (fixed-title row + auto-resolve
  on CLEAN), the hash-dedup design (item 2), the deploy-snapshot decision
  (item 3: yes, scoped `accept` only — wq#3579), and the `aq resolve --help`
  crash routing (wq#3578).
- this evidence note.
