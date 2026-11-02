# wq#4811 verify-pass: wq#4778 (PR lumen/claude-foreman#2576)

Archetype 8 adversarial verify. Author model: claude-sonnet-5. Verifier: claude-sonnet-5@high (fresh instance).

## Verdict: CONFIRM

## What wq#4778 actually shipped

wq#4778 bundled two goals (its own detail text): (1) deploy the khnum
`nodes.ignore` entry (wq#4774, PR #2563) to the live
`/home/lumen/agents/fleet-selfheal` copy, and (2) since the sandboxed BUILD
worker has no ssh/fleet-exec grant onto Lumen and no path inside any worktree
to the deploy target, widen `harness/acceptance.py`'s ops-bundle citation gate
with a `no_deploy_needed` override mirroring `wq done`'s existing
`--no-deploy-needed` (wq#1724/wq#3487), so a worker with a provably correct
config fix isn't stuck unable to self-close (ISSUE-LOG #1636).

Landed as `9d79bebd` (repo shows `4e623d9d` pre-rebase, same content), 4 files:
`harness/acceptance.py`, `harness/fworker.py`,
`harness/tests/test_evidence_gate.py`, `harness/tests/test_crossrepo_citation.py`.

## Code read

- `harness/acceptance.py:811-824` (`_ops_deploy_evidence`): when a citation's
  `deploy_target` is absent, a non-empty `no_deploy_needed` string now passes
  `(True, ..., followup)` instead of failing, where `followup =
  {"bundle": ..., "install_path": ..., "why": ...}`. Crucially this branch only
  reachable when `_check_citation` (acceptance.py:962) has already determined
  the cited file sits inside a recognized `ops/<bundle>/` directory with a
  sibling `install.sh` (`_ops_bundle_install`) — the override cannot be set on
  an arbitrary non-ops citation to dodge unrelated proof requirements.
- `harness/acceptance.py:814`: `no_deploy_needed` is read from `entry` and
  checked for emptiness only — no claim is made that the reason is *true*; the
  accountability mechanism is the follow-up ticket, not the string content.
  Same trust model as the pre-existing `wq done --no-deploy-needed`.
- `harness/acceptance.py:964-968`: when `deploy_target` IS present, the
  `no_deploy_needed` key is never consulted — confirmed by
  `test_ops_install_citation_with_deploy_target_ignores_no_deploy_needed_can_fail`
  (a genuine wq#2630 can-fail probe: supplies both keys, deploy_verify is bad,
  asserts rejection and that `root_apply_followups` never appears).
- `harness/fworker.py:894-928` (`file_root_apply_followups`): only called when
  `result["ok"]` is already True (fworker.py:4962-4967) — followups are never
  filed on a failing replay. Regexes
  `_ROOT_APPLY_ADDED_RE = r"^added task (\d+)"` /
  `_ROOT_APPLY_DUP_RE = r"^DUPLICATE — task (\d+)"` match `/home/lumen/bin/wq`'s
  actual output verbatim (`wq:1764` `added task {tid} [...]`, `wq:1731`
  `DUPLICATE — task {rid} [...]`) — checked directly against the wq source, not
  assumed.
- Filing is best-effort: an unparseable `wq add` output logs and does not fail
  the replay (fworker.py:922-924) — correct, since this is a side-channel
  bookkeeping step layered on top of an already-passing gate.

## Tests

Ran `harness/tests/test_evidence_gate.py` + `test_crossrepo_citation.py` on
HEAD (`9d79bebd`, worktree `/home/lumen/worktrees/wq4811-a2`):

```
83 passed, 1 deselected
```

The 1 deselected (`test_stop_hook_honours_the_hook_contract_as_a_real_subprocess`)
fails identically on the pre-PR parent commit `c63ad913` (verified by checking
out that commit into a throwaway worktree at `/tmp/wq4811-check-parent` and
re-running the same test in isolation — `assert (2 == 0)`, same stderr).
Pre-existing, unrelated to this diff; not a regression from wq#4778.

The two new tests are structurally sound: one is a straightforward
accept-and-surface-followup case, the other is a real wq#2630 can-fail probe
(deploy_target present + bad deploy_verify + a decoy `no_deploy_needed` key) —
not drawn from the population the fix was designed around, and it does fail
without the fix's scoping (`not target` gate).

## Liveness / deploy claim check

wq#4778's own notes do **not** claim the live khnum deploy happened — they
explicitly say "The live khnum deploy itself remains blocked by the same
sandbox gap wq#4774 hit... filed wq#4810 to track it." Checked wq#4810
directly: status `blocked`, and its own notes independently confirm (via
`fleet-exec run lumen cat .../selfheal.yaml`) the live copy still lacks the
khnum entry, and that `ops-deploy-drift.timer` is read-only and cannot apply
it. No contradiction — the ticket is honest about what is and isn't live.

Attempted `bash /home/lumen/foreman/ops/wq3354-fleet-selfheal/install.sh
--verify` myself per the ticket's liveness instruction: refused by this
worker's Bash allowlist (not present in `harness/worker-settings.json`) — same
sandbox boundary wq#4774/wq#4778/wq#4810 all hit. Not retried (refusal is not
an allowlist gap to route around); liveness is already independently tracked
as not-yet-live by wq#4810, consistent with wq#4778's own claim.

## Could not break it

- `no_deploy_needed` is scoped to ops-bundle citations only, not general
  citations — can't be used to bypass an unrelated AC's proof requirement.
- `deploy_target` path always wins over `no_deploy_needed` when both present
  — confirmed by test and by code order (`if not target:` gate).
- Follow-up filing is gated on `result["ok"]` already True and is best-effort
  — a filing failure cannot flip a passing replay to failing, and cannot
  itself forge a pass.
- wq#4778's "done" claim matches what's actually on disk/in wq: the PR
  landed, the new override code is real and tested, and the still-undeployed
  live state is truthfully tracked in a separate blocked ticket rather than
  hidden or falsely marked verified.

No contradiction found. Confirming on the board.
