# wq#3769 — verify-pass on wq#3765 (kv-secret sha256:d1f6f18ff8b9, rotation row)

PR: lumen/claude-foreman#1766, landed on main as `20f08c95` (Refs: wq#3765, wq#3481).
Author: claude-opus-5-5 · Verifier: claude-sonnet-5@high (fresh instance, wq#3769)

## Claim under test

ISSUE-LOG row 1265: `mask_replay.py 3753` flagged a kv-secret value
(sha256:d1f6f18ff8b9, prefix `TOKEN:`, len 28) as `BURNED?` in a live Read of
`/home/lumen/agents/work-queue/boards/LPP_QUEUE_PROGRESS.md` line 1010. wq#3765's
claim: this is not a credential. Line 1010 is review prose — `"...ONE OF THEM
IS NOT A TOKEN: <path> contains the literal `ghp_xxxxxxxx...`, a DOCUMENTATION
PLACEHOLDER in vendored third-party source"` — and the kv rule read `TOKEN: `
plus the vendored Sim Studio source path (`sim-studio-src/.../github.ts`) as
label+value. The board has no git tree to account the value against, so the
replay flags it. Fix shipped: pin the value as committed, intentional text in
`harness/tests/fixtures/wq3753_values.py` + `harness/tests/test_mask_replay_wq3753.py`
(new module, deliberately kept out of `test_mask_replay.py` because that file
carries wq#3481's uncommitted primary-checkout edit — ISSUE-LOG row 1268 — which
blocks `lumen-pr land` on any PR touching it). No code/behavior change, no
rotation. Done notes claim the worktree replay now exits 0.

## What I checked

1. **Found the actual landed diff.** Gitea's HTTP API (`100.121.17.5:3000`)
   returned 404 for both the PR and a repo search, so I reconstructed the diff
   from git directly: `fix/wq3765-rotation-burned-credential-sha256-d1f6f1-a1`
   branched off `46ed4f31`, carries exactly two new files
   (`harness/tests/fixtures/wq3753_values.py`, `harness/tests/test_mask_replay_wq3753.py`,
   59 lines total), and `git diff 20f08c95
   fix/wq3765-rotation-burned-credential-sha256-d1f6f1-a1 -- <those two paths>`
   is empty — the PR branch tip is byte-identical to what's already on `main`.
   So wq#3769's object of verification is exactly commit `20f08c95`, already in
   this worktree's history.
2. **Read both new files cold.** The fixture's value is built from short
   literals (`"sim" "-st" "udi" "o-s" "rc/" "..." "/gi" "thu" "b.t" "s"` →
   `sim-studio-src/.../github.ts`) — matches the path named in the board's own
   (already-public, unredacted) review prose. No new exposure: the string was
   already sitting in plaintext on the board before this PR.
3. **Ran the shipped probe**: `python3 -m pytest
   harness/tests/test_mask_replay_wq3753.py -v` → 2 passed, including
   `test_wq3753_corpus_burned_nothing` (not skipped — `_runs/wq3753` exists on
   this host, so it scored the live corpus, not a stub).
4. **Ran `python3 harness/mask_replay.py 3753` directly**: exit 0, `"5
   credential-shaped values in the run's model context, 0 unaccounted"`. The
   kv-secret value (`sha256=d1f6f18ff8b9`) is shown accounted at
   `harness/tests/fixtures/wq3753_values.py:18`. Matches the done-notes claim
   exactly (prior run exited 1 with a `BURNED?` on this value).
5. **Confirmed the board-side leak path is actually closed**, not just
   asserted: `grep -n "NOT A TOKEN" .../LPP_QUEUE_PROGRESS.md` shows the live
   line now reads `NOT A TOKEN -- sim-studio-src/.../github.ts...` (double
   dash, no `TOKEN: ` colon-space), which the kv rule's `TOKEN: ` + value shape
   no longer matches. This board edit predates wq#3765 (PR#1766 touches no
   board file) — wq#3765 only pins the *historical* corpus hit, consistent
   with its own claim that the leak path was already closed by other means.
6. **Negative control (wq#2630's "a probe must be able to fail")**: edited the
   fixture's sha in place (`d1f6f18ff8b9` → `d1f6f18ff8b0`), reran the same
   two tests — both failed with `AssertionError: ['d1f6f18ff8b0']` (pin
   mismatch) and `_assert_corpus_pinned`'s `records_pin <= shas` assertion.
   Restored the original line; `git status --short` clean again; tests pass.
   This proves the pin is load-bearing, not a vacuous pass.
7. **Checked `_assert_corpus_pinned`/`_unpinned`** (test_mask_replay.py:2021,
   2057) — shared, pre-existing helpers used identically by every prior
   `test_wq*_corpus_burned_nothing` (wq2687, wq2702, wq2913, …). wq#3765 adds a
   new instance of an already-proven pattern; it does not touch the helpers.
8. Checked ISSUE-LOG for any later contradiction or dispute of row 1265/1268 —
   none found (`grep -n "wq#3765\|wq#3769"` turns up only those two rows).

## Verdict

**CONFIRM.** The value flagged by wq#3753's replay is genuinely not a
credential (it's a vendored-source file path quoted in board review prose,
already public on the board in plaintext), the fix pins it correctly (sha
reproduces, corpus accounts for it, `mask_replay.py 3753` now exits 0 where it
exited 1 before), the new test module is isolated from the known-dirty
`test_mask_replay.py` for a disclosed, pre-existing reason (ISSUE-LOG row
1268), and the pin is demonstrably not vacuous (corrupting the sha makes both
new tests fail). No contradiction found — no escalation to judgment.
