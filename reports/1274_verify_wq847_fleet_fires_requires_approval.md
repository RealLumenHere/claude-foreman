# wq#1274 — verify-pass: wq#847 [ops-audit] Reconcile fleet-fires requires_approval config

**Verdict: CONFIRM** — the shipped fix correctly identifies that the original ticket premise
was stale, declines to make the regressive change the ticket literally asked for, and instead
fixes the real stale artifact (a comment). The reasoning, cited commits, and cited tests all
check out against live git history and a fresh test run.

Commit: `26043bc` on `feat/wq847-ops-audit-reconcile-fleet-fires-requires-a3`
(`/home/lumen/foreman`), landed to `main` as `6e8815c` (identical diff, same blob
`ed6f2a5`) via the fleet's live-hotfix-commit path — confirmed by diffing
`origin/main:dispatcher/foreman_dispatcher.py` byte-for-byte against the PR branch. PR:
http://100.121.17.5:3000/lumen/claude-foreman/pulls/201.

## What the diff does

Ticket wq#847 asked to set `fleet-fires.requires_approval: true` in `dispatcher/config.json`
to match code that allegedly "force-checks the approval file regardless." The shipped PR does
**not** touch config.json. Instead it's a 3-line comment-only change to
`dispatcher/foreman_dispatcher.py:88-90` (`FIRE_LANE = ...`), updating a stale comment that
still described pre-`b103366` forced-approval behavior.

## Verification performed

1. **Read the diff cold**: `git show 26043bc` — 1 file changed, 3 insertions(+), 1
   deletion(-), all inside a comment on `FIRE_LANE`. No logic or config touched.
2. **Checked the historical claim**: the commit says `b103366` (2026-08-23 07:27:39, "per
   Frank directive") already removed the MUST-4 hardcode that used to force
   `requires_approval=True` regardless of config. Confirmed `b103366` exists with message
   "AUTO-5 fire-approval gate silently overridden by MUST-4 hardcode + FIRE_LANE fallback
   (both removed per Frank)" — matches the claim, not fabricated.
3. **Checked current code matches the new comment's story**: `foreman_dispatcher.py:678-679`
   reads `fire_exempt = bool(policy.get("requires_approval"))` — config-driven, no hardcode.
   The comment's cross-reference to "line ~196" (`or lane == FIRE_LANE` fallback removed from
   `load_config`) is accurate — confirmed at line 198 in the PR branch.
4. **Checked config wasn't silently changed**: `config.json` `fleet-fires.requires_approval`
   is still `false` on the PR branch — config and code already agree (no gate), so setting it
   to `true` (what the ticket literally asked for) would have reinstated a gate Frank
   explicitly ordered removed — a functional regression, not a fix. Declining to do the
   literal ask was the right call.
5. **Verified cited tests exist at cited lines**: `test_fire_approval_config_honored` and
   `test_fire_approval_when_enabled` are at `dispatcher/tests/test_dispatcher.py:432,450`
   exactly as the commit message states (note: commit message says path
   `tests/test_dispatcher.py`, actual path is `dispatcher/tests/test_dispatcher.py` — minor
   path sloppiness in the message, not a substantive error).
6. **Ran the suite myself** (not just trusted the "58/58" claim): added a detached worktree
   at the PR commit (`git worktree add --detach ... 26043bc`, cleaned up after), ran
   `python3 -m pytest tests/test_dispatcher.py -q` from `dispatcher/` — **58 passed in
   61.20s**, matching the claim.
7. **Checked merge status**: `git merge-base --is-ancestor 26043bc origin/main` says
   false (different SHA — landed via the fleet's live-hotfix path, not a fast-forward
   merge), but a direct content diff of `origin/main:dispatcher/foreman_dispatcher.py`
   against the PR branch shows byte-identical output at the changed lines, and `git log
   origin/main -- dispatcher/foreman_dispatcher.py` shows `6e8815c` — "fix(dispatcher):
   correct stale fire-gate comment", `Refs: wq#847`, same diff, same blob hash `ed6f2a5`.
   The fix is live on `main`; wq#847's done-note "(merged)" is accurate in substance even
   though `26043bc` itself isn't an ancestor of `main`.

## Attempted to break it

- Checked whether the new `FIRE_LANE` comment's factual claims (line ~196 fallback, MUST-4
  removal date/reason) are themselves accurate rather than just plausible-sounding — they are
  (verified against actual line numbers and `b103366`'s real commit message).
- Checked whether the "58/58 pass" and cited test line numbers were trustworthy claims or
  copy-paste — ran the suite fresh myself rather than accepting the commit message.
- Checked whether "PR merged" was true given `is-ancestor` initially returned false — traced
  it to the live-hotfix-commit mechanism (distinct SHA, identical tree) rather than treating
  the SHA mismatch as evidence of an unlanded fix.
- No contradiction found. Nothing here required opus-5 escalation.

## Findings log

kb/ISSUE-LOG.md#47 (pre-existing, filed by the wq#847 author) already documents this
reconciliation accurately; no correction needed there.
