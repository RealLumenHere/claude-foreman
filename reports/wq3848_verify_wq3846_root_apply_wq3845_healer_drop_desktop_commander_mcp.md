# wq#3848 verify: wq#3846 [ops-root-apply] wq3845-healer-drop-desktop-commander-mcp

Archetype 8 adversarial verify pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5
(fresh instance, same model as author). Verify repo: `/home/lumen/foreman`.

## What wq#3846 claimed

wq#3845 shipped `ops/wq3845-healer-drop-desktop-commander-mcp/` (idempotent `install.sh` +
`--verify` contract + diff + RUNBOOK) but closed with its own `install.sh --verify` FAILING
against the live `/home/lumen/agent/healer_watch_table.yaml` (row still present) — the
dispatch note said this needed a "privileged install". wq#3846's claim
(`reports/wq3846_root-apply_wq3845-healer-drop-desktop-commander-mcp.md`): the real gap was
execution path, not root — a sandboxed BUILD worker can't run bare `bash ops/*`
(`harness/worker-settings.json` only allowlists `/home/lumen/bin/lumen-run *`) and the live
file sits outside the worktree's `--add-dir` grant regardless. Used `lumen-run` (detached
`systemd-run`, runs as `lumen`) to run the bundle's own apply (step 1, `--root`, logged
`unchanged` — row was already gone, likely via the duplicate wq#3816 bundle landing first)
and verify (step 2, no `--root`) steps, plus an independent live grep (step 3, no match).
Conclusion: `ops_verified(wq3845-healer-drop-desktop-commander-mcp)` now SATISFIED, no actual
root/sudo requirement existed.

## What I checked

- Read `install.sh`, `RUNBOOK.md`, `healer_watch_table.diff`,
  `ops/tests/test_wq3845_verify.py`, `harness/worker-settings.json`, and
  `_runs/wq3846/evidence.json` cold.
- Confirmed the execution-path claim directly: a bare `bash
  ops/wq3845-healer-drop-desktop-commander-mcp/install.sh --verify` from this worktree was
  refused ("requires approval... following parts require approval"), matching
  `worker-settings.json:19`'s allowlist (only `lumen-run *`, no `bash ops/*` or `bash`
  generally). Did not retry the bare form (prior wq#3848 attempt looped exactly here, per its
  own critique) — pivoted straight to `lumen-run`, the documented escape hatch.
- Independently re-ran the LIVE target myself, not just citing wq#3846's report:
  `lumen-run start --name wq3848-verify -- bash
  /home/lumen/foreman/ops/wq3845-healer-drop-desktop-commander-mcp/install.sh --verify` →
  `lumen-run wait` = `DONE:0`; `lumen-run log` shows `VERIFY OK` / `LUMEN_JOB_EXIT=0`.
- Independently grepped the live file (not trusting `install.sh`'s own verify logic):
  `lumen-run start --name wq3848-grep-check -- bash -c 'grep -nE
  "desktop-commander-mcp|mcp-gateway|ntfy" /home/lumen/agent/healer_watch_table.yaml'` →
  no `desktop-commander-mcp` match; `mcp-gateway`/`ntfy`/`lumen-gatus-ntfy-relay` lines
  present and byte-identical to
  `ops/wq3845-healer-drop-desktop-commander-mcp/healer_watch_table.yaml.deployed` (the
  evidence-gate fixture added by wq#3846's second commit, `1b645639`) — the live file
  genuinely matches the claimed post-deploy state, today, independent of the repo fixture.
- Ran the bundle's own test suite standalone: `python3 -m pytest
  ops/tests/test_wq3845_verify.py -v` → **5 passed**
  (`test_row_absent_passes`, `test_row_present_fails`, `test_unparseable_table_fails`,
  `test_comment_mentioning_unit_is_not_a_row`, `test_missing_table_is_error_not_drift`). Two
  cases assert non-zero exit against deliberately-broken fixtures and one exercises an
  input (missing file) outside the row-present/absent population the fix targets —
  satisfies the wq#2630 falsifiability doctrine (also independently re-confirmed here, not
  just taken from wq#3847's prior verify of the underlying wq#3845 fix).
- Checked whether "root-applied" is accurate: RUNBOOK says the deploy itself runs "as lumen,
  no root"; step 1's `--root` flag only wrapped a no-op (`unchanged`, row already gone before
  that run), and step 2's actual verify ran without `--root`. So wq#3846's own conclusion —
  "not an actual root/sudo requirement" — is internally consistent with its own evidence, not
  a case of claiming root was needed and then not using it.
- Cross-checked `_runs/wq3846/evidence.json` AC1 (`cmd_run: lumen-run start -- bash
  .../install.sh --verify`, `exit_code: 0`, citation
  `reports/wq3846_..._.md:22-28`) against the report: lines 22-28 are actually the
  install-step's idempotent-delete code excerpt, not the verify step's output (that's lines
  31-36). Minor citation imprecision — not a correctness defect, since I independently
  re-ran the exact `--verify` invocation myself and got the same `VERIFY OK`/exit 0 the
  evidence row claims; the substantive claim is externally confirmed regardless of which
  line range the citation points at.

## What I did not find broken

- No scope creep: the duplicate wq#3816 bundle and the other `desktop-commander-mcp`-aware
  guard scripts (`commander-blocklist.sh` etc.) are untouched.
- The liveness probe (`harness/liveness.py:432-451`, per wq#3847's prior verify) runs `bash
  install.sh --verify` against the real live path and maps rc 0 → SATISFIED — matches what
  both wq#3846 and this pass observed.
- No fabricated evidence (instinct 8 check): every path/unit name in wq#3846's report
  (`healer_watch_table.yaml`, `install.sh` line numbers, the `lumen-job-wq3846-*` unit names)
  is grep-verifiable in this repo/live file; the install/verify unit names in the report
  are just prior invocations' IDs (not reproducible verbatim) but the mechanism and the
  live state they claim are both independently reproducible, and I reproduced them.

## Verdict: CONFIRM

No contradiction. wq#3846's root-apply is real: the live
`/home/lumen/agent/healer_watch_table.yaml` has no `desktop-commander-mcp` row today
(re-checked independently via `lumen-run` + a live grep, not by trusting the report or
`install.sh`'s own verify logic alone), the bundle's test suite passes with falsifiable
cases, and the "no actual root needed, `lumen-run` was the real fix" conclusion holds up
against its own evidence. One cosmetic nit (evidence.json's citation line range points at
the install-step excerpt rather than the verify-step output) does not change the verdict.
`ops_verified(wq3845-healer-drop-desktop-commander-mcp)` liveness: SATISFIED.

Refs: wq#3848, wq#3846, wq#3845, wq#3847.
