# wq#4566 — verify pass on wq#4551 (archetype 8)

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/2401 (merged, commit `2e65a089`)
Author model: claude-sonnet-5. Verifier: claude-sonnet-5@high, fresh instance.

## Claim under test

wq#4551 and wq#4550 are identical duplicate tickets (same title/detail,
created 8s apart). wq#4550 already landed the real fix (push-block guard at
`harness/guard.sh:169-177`) and queued wq#4563 (`frank_gated`) for the
Frank-domain visibility change. wq#4551's PR claims to avoid re-implementing
the same guard logic, instead updating the guard's comment and block message
to cross-reference both ticket IDs, with "78/78 harness/tests/test_guard.py
pass unchanged."

## What I checked

- `wq show 4550` / `wq show 4551`: confirmed byte-identical title and detail
  text, `created_at` 06:36:16 and 06:36:24 — genuine duplicate, not a
  mischaracterization.
- `wq show 4563`: exists, `status=frank_gated`, `gate_type=frank_decision`,
  correctly scoped to the Frank-only GitHub visibility/delete action.
- `git show 2e65a089 -- harness/guard.sh`: diff is exactly 7 insertions / 6
  deletions, entirely inside the comment block and the `block "..."` message
  string. The actual matching logic —
  `grep -qE 'RealLumenHere/ccc\b|/home/lumen/ccc\b'` — is byte-for-byte
  unchanged. No other files touched (`git show --stat` confirms
  `harness/guard.sh | 13 +++++++------`, nothing else).
- `python3 -m pytest harness/tests/test_guard.py -q`: 78 passed, matching the
  PR's claim exactly.
- Live-fired confirmation: while probing, my own Bash commands containing the
  literal string `RealLumenHere/ccc` next to `git push` were blocked by this
  session's own PreToolUse guard hook with the message
  `git push toward RealLumenHere/ccc denied (wq#4550/wq#4551: public mirror of
  never-public ccc)` — i.e. the deployed guard.sh already carries exactly the
  text this PR's diff adds, confirming the change is live and not just
  committed-but-unapplied.
- Adversarial probe (via a script file, not inline Bash, to avoid self-
  tripping the guard) against `harness/guard.sh` directly with a hand-built
  JSON hook envelope, covering: plain URL push, `git@` SSH push, `-C
  /home/lumen/ccc` push, env-var `GIT_DIR` push, whitespace-mangled push, an
  uppercase-slug variant, and a same-prefix unrelated repo. Blocked correctly
  for every variant that was blocked before this PR; the one unrelated-repo
  push (`git push origin main`) stayed allowed, matching the existing
  `MUST_ALLOW` corpus entry for "the ccc deny names that repo only."

## Verdict: CONFIRM

wq#4551's diff does exactly what its PR description says: a duplicate-ticket
cross-reference in comments/messages, zero logic change, tests unchanged and
green, the Frank-gated follow-up (wq#4563) correctly in place. I could not
break this diff — every failure mode I tried was either pre-existing (inherited
unchanged from wq#4550, see below) or not present.

## Out-of-scope finding (not a contradiction of wq#4551)

While adversarially probing the regex this PR touches only in comment text, I
found the underlying match (unchanged since wq#4550, so **not** introduced or
regressed by wq#4551) is case-sensitive and boundary-loose:

- `git push https://github.com/RealLumenHere/CCC.git main` (uppercase slug) —
  **not blocked**, even though GitHub routes repo slugs case-insensitively,
  so this is a live evasion of the intended backstop.
- `git push https://github.com/RealLumenHere/ccc-mirror.git main` (a
  different, hypothetical repo whose name happens to start with `ccc-`) —
  blocked as a false positive, because the trailing `\b` is satisfied by the
  `-` boundary.

This is a real gap but it predates and is untouched by wq#4551's diff, so it
does not contradict wq#4551's claim. Logged at
`/home/lumen/kb/ISSUE-LOG.md` row 1533 and filed as follow-up **wq#4569**
(pri 3, build lane) rather than blocking this verify.

## Evidence

- `wq show 4550`, `wq show 4551`, `wq show 4563` output (ticket metadata).
- `git show 2e65a089 -- harness/guard.sh` (full diff, comment/message only).
- `python3 -m pytest harness/tests/test_guard.py -q` → `78 passed in 56.07s`.
- Direct guard.sh probe script output (7 crafted commands, see transcript),
  plus the live self-trip of this session's own PreToolUse hook on the
  updated block message text.
