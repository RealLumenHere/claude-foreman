# wq#3185 — verify wq#3161 [test] test_wq2253_sk_key_is_absent_from_the_model_context red on live ISSUE-LOG.md

Archetype 8 adversarial verify pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance, same model as author).

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1443 (commit `61f1b227`), landed on
main as `b68683fa` (empty diff — see "Duplicate fix" below). File touched:
`harness/tests/test_mask_replay.py` (+7/-0), inside
`test_wq2253_sk_key_is_absent_from_the_model_context`.

## Claim under test

wq#3161 (found by wq#3083): the test asserted the wq2253 `sk-key` values are found ONLY in
the frozen doctrine snapshot, but `mask_replay.corpus(2253)` also yields a
`read-stale:/home/lumen/kb/ISSUE-LOG.md@658#unresolved` source — today's live text of a
mutable file standing in for bytes the run's Read call actually saw — which also scores a
hit, so the test goes red whenever `kb/ISSUE-LOG.md` grows past its recorded line 658. Fix:
skip any corpus source keyed `read-stale:` in the test's per-source scoring loop.

## What I checked

- Read the diff cold (`git show 61f1b227`): a single `if source.startswith("read-stale:"):
  continue` guard added ahead of the existing per-source `replay()` call, with a comment
  citing `mask_replay._recut`.
- Read `mask_replay.corpus()` (`harness/mask_replay.py:246-303`) and `_recut()`
  (`:347-373`): the docstrings independently confirm the fix's premise — a `read-stale`
  window is "today's text standing in for bytes that are gone... the record it produces is
  evidence of nothing" when no git revision at the recorded file size can be found. The
  `replay()` function's `"faithful"` field (`:753`) already treats `read-stale`-sourced hits
  as non-authoritative elsewhere in the same module, so the test's new exclusion is
  consistent with existing production semantics, not an invented workaround.
- **Falsifiability probe (required by doctrine — an acceptance probe must be able to
  fail):** temporarily reverted the 7-line guard in the worktree, confirmed the test
  actually goes red with exactly the failure shape the ticket describes —
  `found` picks up an extra `read-stale:/home/lumen/kb/ISSUE-LOG.md@658#unresolved` key
  alongside the doctrine snapshot, failing
  `assert found == {WQ2253_DOCTRINE_SNAPSHOT: set(WQ2253_SK_KEY)}`. Restored the file from
  a pre-edit backup immediately after (`git status --short` clean, confirmed byte-identical).
- Ran the target test with the real fix in place:
  `python3 -m pytest harness/tests/test_mask_replay.py -k
  test_wq2253_sk_key_is_absent_from_the_model_context -v` → **1 passed**. `WQ2253_RUN`
  (`/home/lumen/foreman/_runs/wq2253`) exists on this host, so the `skipif` guard did not
  mask the run — this was a real pass, not a skip.
- Scope check: `git show --stat 61f1b227` — only the one test file, only the one hunk. No
  production code (`mask_replay.py`, `masking.py`) touched, so the exclusion cannot weaken
  the live masking/tripwire path; it only prevents this regression test from scoring a
  source the module's own docs already call "evidence of nothing."
- Kicked off the full `harness/tests/test_mask_replay.py` suite (118 tests) in place as an
  independent regression check; output is fully-buffered (redirected, non-tty) so no
  interim lines are visible before it exits. Not blocking the verdict on it: the ticket's
  own scope is this one test, already directly verified above, and wq#3161's done-notes
  independently record a pre-merge run of the same suite "clean through 45%+/116 with zero
  failures," plus wq#2959 (the ticket that shipped the identical hunk moments earlier)
  recorded a full-repo run at "157 passed 1 skipped."

## Duplicate fix (not a defect, but worth recording)

wq#2959 (a different, earlier ticket: "two mask gates are red on main independently of any
masking change") shipped the **byte-identical** hunk to the same test — same blob hashes
(`1796c7fd..c4aa935a`), same comment text, same line numbers — via PR 1438, merged as
`c023f7c86c` at 22:13:59, *before* wq#3161 was even claimed (22:18:44). Both tickets were
independently dispatched against the same red test and converged on the same fix. When
wq#3161 landed (22:32:30, commit `b68683fa`), the target content was already on main from
wq#2959, so the landing commit is an empty no-op (`git diff b68683fa~1 b68683fa` is empty) —
lumen-pr recorded the ticket's closure without introducing a duplicate hunk or corrupting
the file. Confirmed current main (`0e26f567`) has the guard exactly once. No functional
risk; flagging only so the duplicate-dispatch pattern (wq#2959 and wq#3161 racing the same
red test) is visible for triage, not because the shipped code needs anything further.

## Verdict: CONFIRM

wq#3161's fix does what it claims: it excludes the mutable-file `read-stale:` corpus source
from the test's evidence set, matching the module's own documented semantics for that source
kind, proven falsifiable (fails without the guard, passes with it), and scoped to test code
only. The only anomaly — an empty landing commit — is explained by an identical fix already
merged from wq#2959 moments earlier, not by any fault in wq#3161's diff. No contradiction
found; no escalation to opus-5@high.
