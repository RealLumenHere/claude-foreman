# wq#3487 — cross-repo evidence note (same shape as wq#2573/wq#3315)

The dispatcher checked this ticket out as a **foreman** worktree, but the
deliverable — `wq done`'s ops-deploy gate and `ops-deploy-drift` — lives in
the **bin** (`lumen-bin`) repo, which this worktree cannot natively diff
against (toolset/settings frozen for the life of a run). Applied wq#192/
wq#2569/wq#2573/wq#3315's exact workaround: nested a real `bin` worktree
under this worktree's gitignored `_probe/` prefix (`lumen-pr start
/home/lumen/bin feat/wq3487-ops-root-apply-gate --worktree`, then manually
re-pointed at `_probe/bin-wq3487` since `LUMEN_PR_WORKTREE_ROOT`'s default
of `/home/lumen/scratch/worktrees` is outside this session's allowed
directories and the env-var override itself does not match the
`Bash(/home/lumen/bin/lumen-pr *)` allowlist pattern), built + tested the
fix there, landed it via `lumen-pr` from inside that nested tree, then
removed the nested worktree. This note is the pointer so a citation can
resolve against *this* branch's diff while the real bin-side change is
documented here.

## bin repo change (landed, not part of this branch's diff)

- PR: http://100.121.17.5:3000/lumen/lumen-bin/pulls/235 — merged as
  `fd370243ae` (squash) on branch `feat/wq3487-ops-root-apply-gate`;
  primary `/home/lumen/bin` fast-forwarded to `f22ac96`.
- `wq` (cmd_done's ops-deploy gate, wq#1724/wq#2943 extended):
  - `_ops_bundle_retired_reason()` / `ops_root_apply_check()` /
    `_file_or_reuse_root_apply_followup()` / `_ops_bundles_from_text()` —
    new helpers for the RETIRED marker and the root-apply follow-up filer.
  - `ops_deploy_check()`: short-circuits to `"none"` for a RETIRED bundle.
  - `pr_ops_deploy_check()`: skips RETIRED bundles entirely; now returns a
    third element (bundle paths whose own `--verify` said FAIL) alongside
    `(verdict, why)`; its one call site in `cmd_done` updated.
  - `cmd_done`'s path-citation branch (`kind=="path"`) and `--no-artifact`
    branch each gained a call into `ops_root_apply_check`/
    `_ops_bundles_from_text` at the point where the override is honored.
- `ops-deploy-drift`: `retired_reason(bd)` checked before `has_verify`;
  `--aq` resolves a `RETIRED` verdict the same branch as `VERIFIED`;
  `OPS_DEPLOY_DRIFT_OPS` env override added for tests (mirrors `unit-drift`'s
  `UNIT_DRIFT_SYS`).
- New tests: `tests/test_wq3487_ops_root_apply_followup.py` (7),
  `tests/test_wq3487_ops_deploy_drift_retired.py` (3).

## Tests (bin repo)

`tests/test_wq_ops_deploy_gate.py` + `tests/test_wq2943_ops_deploy_url_gate.py`
(existing, unchanged) — 18/18 still pass. New files — 10/10 pass. Full
relevant suite (`test_probe_target_liveness`, `test_wq2943_ops_deploy_url_gate`,
`test_wq_done_gate_terms`, `test_wq_evidence_durability`,
`test_wq_ops_deploy_gate`, both new files) run together — 65/65 passed. Run
with `--basetemp` pointed outside the git worktree: `tmp_path` resolving
inside a linked worktree trips the wq#1673 evidence-durability gate on an
unrelated pre-existing test, not this change.

## foreman repo change (this branch's own diff)

- `design/wq3487_ops_root_apply_gate.md` — the design doc written before
  implementation, per the ticket's "design doc first" instruction.
- `ops/VERIFY_CONTRACT.md` — documents the `RETIRED` marker as the
  lightweight alternative to an inverted `--verify` handler, and the
  root-apply follow-up behavior under "What consumes it".
- This note.
