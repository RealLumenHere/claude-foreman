wq#4260 dispatched `ops/wq4258-fleet-consolidation-plan/install.sh --verify`'s
FAIL output as "needs privileged install" and asked for a root/sudo run.

That reading doesn't hold up: this bundle has no automated install step at
all (README.md "Phase 2") -- the merge into `/home/lumen/agent/litellm_config.yaml`
is a manual hand-merge, blocked on `ANTHROPIC_API_KEY` landing via a
Frank-only credential mint. The MISS/FAIL on the two `claude-sonnet` /
`claude-haiku` markers is the correct, expected state until that key lands;
re-running as root would not change anything because the script performs no
install step either way.

Added a one-line NOTE to the `--verify` branch of install.sh so the real
cause is visible in the verify output itself, instead of relying on a reader
to already know the README context. Logged the misdispatch in
`kb/ISSUE-LOG.md` row 1431 (separate repo, not part of this diff).

Evidence: `python3 -I ops/tests/test_wq4258_fleet_consolidation_verify.py -v`
-- all 5 existing verify-contract tests still pass against the fixture
config (`ops/tests/test_wq4258_fleet_consolidation_verify.py`), confirming
the NOTE line doesn't break the FAIL/PASS contract.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
