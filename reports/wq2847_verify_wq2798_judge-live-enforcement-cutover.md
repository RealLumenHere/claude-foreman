# wq#2847 — verify-pass: wq#2798 judge live-enforcement cutover

Archetype: verify-pass (adversarial, cold read). Author: claude-sonnet-5. Verifier:
claude-sonnet-5@high, fresh instance, same model as author. Diff: PR
http://100.121.17.5:3000/lumen/claude-foreman/pulls/1167, merged as `b3e05d7`
(`harness/action_gate.py`, `harness/tests/test_action_gate.py`), ancestor of
`main` at verify time (`git merge-base --is-ancestor b3e05d7 main` → true).

## Claim under test

wq#2798: wire `write_red_handoff`'s RED flag file into a live call to
`bin/judge`, turning a RED verdict into deny (unchanged hard block) or
allow / allow-with-conditions (staging named safeguards before letting the
action through), gated behind `FOREMAN_JUDGE_ENFORCE` default `"0"`, fail-closed
on any judge malfunction. Notes claim "78 tests pass (7 new)".

## What I checked

- Read `harness/action_gate.py` cold: `_judge_enforce_enabled` (:87-88),
  `_call_judge_bin` (:675-702), `enforce_judge_verdict` (:772-801),
  `stage_judge_conditions` (:731-754), and the `hook_main` RED branch that
  wires them in (:1006-1027).
- Cross-checked the `bin/judge` CLI contract action_gate depends on
  (`--actor/--kind/--command/--context/--gate-verdict/--timeout`, JSON-only
  stdout, `FAIL_CLOSED` verdict on failure) against the real binary at
  `/home/lumen/bin/judge` (args :119-126, JSON emission :112/:157) — the
  contract is not just asserted by a test double, the fake judges in the test
  file emit the same shape the real binary emits.
- Ran the real suite: `python3 -m pytest tests/test_action_gate.py -q` from
  `/home/lumen/foreman/harness` → **78 passed**, matching the PR's claimed
  count exactly (7 new: `test_judge_enforce_off_by_default_still_hard_blocks_
  even_if_judge_would_allow`, `..._allow_lets_the_action_through`,
  `..._deny_still_hard_blocks`, `..._fail_closed_still_hard_blocks`,
  `..._missing_binary_fails_closed`, `..._allow_with_conditions_stages_
  snapshot_before_allowing`, `..._condition_staging_failure_fails_closed` —
  `test_action_gate.py:247-341`).
- Verified the kill switch is inert in practice, not just in code: grepped
  the whole `foreman` and `bin` trees for `FOREMAN_JUDGE_ENFORCE` — the only
  hits are the definition/tests themselves, no systemd unit, `.env`, or
  wrapper script sets it to `1` anywhere in-repo.
- Traced every non-allow exit: broken/unreachable judge, an unrecognised
  verdict string, and a forced condition-staging failure (missing `cwd`
  breaks the git-fallback pre-image, exercised by the test at
  `test_action_gate.py:330-341`) all land on `{"action": "deny", ...}` in
  `enforce_judge_verdict`, which `hook_main` treats identically to an
  ordinary unconditional RED (`RED_MSG`, exit 2) — no code path returns 0
  without the allow/allow-with-conditions branch being hit.
  `allow-with-conditions` calls `stage_judge_conditions` (which reuses the
  same `snapshot()` AMBER already depends on) and only returns exit 0 after
  staging succeeds — matches the stated AMBER invariant ("never execute
  unsnapshotted").
- Read `kb/ISSUE-LOG.md#867`, cited in wq#2798's own done-notes as the
  reasoning record. It documents that wq#2801 (Frank's tier-policy sign-off)
  is still open and shadow-mode has only 4 non-production RED rows, and it
  explicitly ties "shipped default-off" to that gap. This matches the code's
  own inline rationale comment (`action_gate.py:72-81`) verbatim in spirit —
  not a case of the done-notes claiming caution the diff doesn't back up.

## Attempts to break it

- Tried to find an enable-by-accident path (env var typo tolerance, a
  competing default, a config file already flipping it) — found none;
  `_judge_enforce_enabled` requires the literal string `"1"`.
- Tried to find a verdict value that slips past the allow-list check in
  `enforce_judge_verdict` (`out.get("verdict") not in ("allow", "deny",
  "allow-with-conditions")`) — any other string (typo, case mismatch, empty)
  fails closed by construction.
- Tried to find a case where `allow-with-conditions` could return exit 0
  before staging — `stage_judge_conditions` runs synchronously inside
  `enforce_judge_verdict` before the dict with `action: allow-with-conditions`
  is returned, and `hook_main` doesn't return 0 until it receives that dict.
  No ordering bug.
- Considered whether `ssh(anubis)` (auto-granted on this ticket, presumably
  from the PR URL's host) hid a live-infra check I was missing — found none:
  wq#2798 is code-only, its own done-notes say flipping the env var is "the
  only action left, not a code change," and that hasn't happened (no
  in-repo or systemd config sets the flag). The two SSH probe commands I
  tried against anubis were both refused by this worker's Bash allowlist
  (`ssh lumen@100.121.17.5 '...'` → "This command requires approval") before
  I could even test connectivity, so I did not spend further attempts
  retrying an unapprovable shape (per wq#1107) — noting it here per the
  RULES ("say so plainly... so the allowlist can be widened") rather than
  treating it as a blocker, since local git + repo evidence already fully
  settles the ticket's claims without it.

## Verdict: CONFIRM

The diff does what wq#2798 and its done-notes claim: live Layer 4 enforcement
code that is real, tested, and fail-closed, shipped inert behind a default-off
kill switch pending wq#2801. No contradiction found; no escalation to
opus-5@high triggered.
