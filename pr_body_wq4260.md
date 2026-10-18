## What

Add a one-line NOTE to `ops/wq4258-fleet-consolidation-plan/install.sh`'s
`--verify` branch explaining that a MISS/FAIL on the `claude-sonnet` /
`claude-haiku` markers is the expected, credential-gated state, not a
privileged-install problem.

## Why

wq#4260 dispatched this bundle's `--verify` FAIL as "needs privileged
install" and asked for a root/sudo run. That reading doesn't hold up: this
bundle has no automated install step at all (README.md "Phase 2") -- the
merge into `/home/lumen/agent/litellm_config.yaml` is a manual hand-merge,
blocked on `ANTHROPIC_API_KEY` landing via a Frank-only credential mint
(instinct 3 physical dependency, already queued). The MISS/FAIL is correct
and expected until that key lands; re-running as root would change nothing
because the script performs no install step either way. Logged the
misdispatch in `kb/ISSUE-LOG.md` row 1431 (separate repo, not part of this
diff) so the next dispatch reads the real cause first.

## How

Added a comment + `echo "NOTE: ..."` line inside the existing
`if [ "${1:-}" = --verify ]` block, printed after the two `vcontains` checks
and before `vdone`, so it shows up in the verify output on both FAIL and
(once the key lands) OK runs.

## Testing

`python3 -I ops/tests/test_wq4258_fleet_consolidation_verify.py -v` -- all 5
existing verify-contract tests pass unchanged against the fixture config,
confirming the NOTE line doesn't alter the FAIL/PASS/exit-code contract
(`vcontains`/`vdone` behavior, marker-precedes-install-step ordering).

## Risk / rollback

Doc-only change to a single echo line inside a read-only `--verify` branch;
no install/runtime behavior changes. Revert is a one-line removal if ever
needed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
