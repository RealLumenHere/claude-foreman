# wq#3579 — cross-repo evidence note (same shape as wq#2569/wq#2573/wq#3509)

The dispatcher checked this ticket out as a **foreman** worktree, but the code
half of the deliverable (`unit-drift accept`) lives in the **lumen-bin** repo.
Applied the wq#192/wq#2569/wq#3509 workaround: nested a real bin worktree under
this worktree's gitignored `_probe/` prefix, built + tested the verb there,
landed it via `lumen-pr` from inside that nested tree, then removed the nested
worktree. This note is the pointer so a citation can resolve against *this*
branch's diff for AC1 while the real bin-side change is documented here.

## lumen-bin change (this ticket's code, not part of this branch's diff)

- PR: http://100.121.17.5:3000/lumen/lumen-bin/pulls/239 — branch
  `feat/wq3579-unit-drift-accept`, squash-merged as `a9ddd0b0f7`;
  `/home/lumen/bin` fast-forwarded to `e086a66`, so the verb is live.
- `unit-drift accept --ticket <wq-id> <unit>...` (`cmd_accept`): updates
  units/, dropins/, enable-state.tsv and MANIFEST.json for the **named** units
  only — unit file or `.symlink` marker, `<unit>.d/` drop-ins, enable links
  whose target is the unit; removes each from the baseline when gone from
  disk. Commit message carries ticket id + unit list
  (`accept wq#NNNN: foo.service foo.timer`). Refuses a dirty fleet-units repo
  (exit 2) so the commit can never smuggle unrelated drift; refuses missing
  `--ticket` / non-unit names; no empty commit when the baseline already
  matches. `REPO` gained the `UNIT_DRIFT_REPO` env override (tests-only, same
  pattern as `UNIT_DRIFT_SYS`).
- Tests: `tests/test_wq3579_unit_drift_accept.py` (7 new) — core property
  `test_undeclared_drifting_unit_survives_accept_of_declared_one`. With the
  neighboring wq3466/wq3509/wq3099 suites: 20 passed.
- Live probes after the fast-forward: `accept` with no args / a non-unit name
  → exit 2; `accept --ticket wq#3579 alert-deadman-beat.timer` (clean unit) →
  "baseline already matches", exit 0, fleet-units HEAD unchanged
  (`0e7b10ade8d7`); the 21 currently-drifting undeclared units were left
  drifting.

## foreman repo change (this branch's own diff)

- `ops/VERIFY_CONTRACT.md` — new section "Bundles that ship systemd units:
  declare them, then accept them (wq#3579)": the `UNITS=` declaration reviewed
  with the bundle PR, `unit-drift accept --ticket wq#NNNN $UNITS` as the last
  install step, and why a blanket `snapshot` is banned in install.sh.
- this evidence note.
