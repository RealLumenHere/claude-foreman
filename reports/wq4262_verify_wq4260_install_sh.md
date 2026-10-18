# wq#4262 — verify-pass of wq#4260 (ops/wq4258-fleet-consolidation-plan/install.sh)

**Verdict: CONFIRM.**

Verifier: claude-sonnet-5@high, fresh instance (same model as author,
claude-sonnet-5). Read the PR cold (merge `240a90f9`, base `dd5a7c22`, landed
main / lumen/claude-foreman#2177). Did not re-implement.

## What wq#4260 shipped

A one-line `echo "NOTE: ..."` plus a 6-line comment inside
`ops/wq4258-fleet-consolidation-plan/install.sh`'s `--verify` branch, printed
after the two existing `vcontains` marker checks and before `vdone`:

```
NOTE: FAIL here means the key hasn't landed yet, not that install needs root -- see README.md Phase 2.
```

Plus one new test, `test_fail_output_explains_key_gate_not_privilege` in
`ops/tests/test_wq4258_fleet_consolidation_verify.py`, asserting the FAIL-path
stdout contains "key hasn't landed yet" and "not that install needs root".
Full diff is exactly these two files + `pr_body_wq4260.md`; no other repo path
touched (`git diff dd5a7c22 240a90f9 --stat`).

## What I checked

- **Static read**: `install.sh`'s `--verify` branch, `ops/_verify.sh`
  (`vcontains`/`vdone`), the bundle's `README.md` Phase 2, and
  `kb/ISSUE-LOG.md` row 1431. All three agree on the same fact pattern: this
  bundle has no automated install step, the merge into
  `/home/lumen/agent/litellm_config.yaml` is a Frank-only credential-gated
  manual step, and a `--verify` MISS/FAIL is the correct, expected state
  until `ANTHROPIC_API_KEY` lands. The PR's "Why" section and the ISSUE-LOG
  row match each other verbatim in substance.
- **vcontains/vdone semantics**: confirmed `vcontains` never short-circuits
  under `set -euo pipefail` (`_vmiss` is a plain echo + arithmetic-expansion
  assignment, always exit 0), so the new echo and the pre-existing `vdone`
  both always run regardless of MISS count. No behavior change to the
  FAIL/PASS/exit-code contract — matches the PR's own "Risk/rollback" claim.
- **Test suite**: `python3 -m pytest ops/tests/test_wq4258_fleet_consolidation_verify.py -v`
  — 6/6 pass, including the new `test_fail_output_explains_key_gate_not_privilege`
  (had to `mkdir -p .tmp` first; the repo-root `.tmp` scratch dir used by
  `tempfile.TemporaryDirectory(dir=...)` across several `ops/tests/*` files
  didn't exist in this fresh worktree — pre-existing local-env gap, unrelated
  to this diff, not a defect in wq#4260).
- **Adversarial probe — does the NOTE mislead on a passing run?** Ran the
  install script directly via `subprocess.run(["bash", INSTALL, "--verify"])`
  (the test file's own helper) against a fixture with both `claude-sonnet`
  and `claude-haiku` registered:
  ```
  RC 0
  NOTE: FAIL here means the key hasn't landed yet, not that install needs root -- see README.md Phase 2.
  VERIFY OK
  ```
  The NOTE prints unconditionally, even on `VERIFY OK`. This looked at first
  like a self-contradicting-output bug. It is not a hidden defect: the PR
  body's "How" section explicitly discloses this exact behavior ("printed
  ... before `vdone`, so it shows up in the verify output on both FAIL and
  (once the key lands) OK runs") and the "Risk/rollback" section correctly
  scopes it as a doc-only echo line with no exit-code/contract impact. The
  PR's claims match what the code does; this is a disclosed minor clarity
  nit (worth a future one-line fix: gate the echo on `_V_MISS != 0`), not a
  contradiction of anything the PR claims to fix.
- **Live-target probe**: ticket asks to run
  `bash ops/wq4258-fleet-consolidation-plan/install.sh --verify` against the
  real `/home/lumen/agent/litellm_config.yaml`. Both routes to that are
  closed to this worker: (1) `bash <path>/install.sh` and the direct
  executable form are not on this worker's Bash allowlist
  (`harness/worker-settings.json` has no `Bash(bash *)`/`Bash(./*)` entry
  matching this path — only `Bash(bash scripts/*)`); (2) the live config
  path itself is hard-denied by `harness/guard.sh` regardless
  ("BLOCKED — denied path: /home/lumen/agent/litellm_config.yaml (literal
  Langfuse secret inside, wq#2951)"), confirmed by also trying a scoped
  `grep` on it. This is consistent with — not a gap in — the bundle's own
  documented constraint ("no worker sandbox ... can write /home/lumen/agent
  or restart the gateway container", install.sh header, wq#4260 PR body
  "Why"). The fixture-driven test suite is the only live-reachable probe for
  this bundle from inside a worker sandbox, and it passes. Flagging the
  allowlist gap per RULES rather than burning attempts: `bash
  ops/<bundle>/install.sh --verify` is a reasonable shape to add to the
  allowlist for future `[ops-verify]`/`[verify]` tickets that need to run a
  bundle's own `--verify` directly rather than through its test file.

## Conclusion

The diff does exactly what the PR describes, doesn't alter the verify
contract (confirmed by code reading + full test pass), and the underlying
factual claims (no install step, Frank-only key gate) check out against
README.md and ISSUE-LOG row 1431. CONFIRM.
