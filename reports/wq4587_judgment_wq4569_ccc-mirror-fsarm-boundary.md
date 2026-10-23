# wq#4587 — Judgment pass (archetype 8, opus-5@high): wq#4569 ccc-mirror block regex

**Verdict: CONFIRM.** wq#4569's closure stands. The diff correctly fixes the bug it
describes. A fresh, previously-undiscovered twin of the same defect mechanism exists
one line below the line wq#4569 touched, but it falls outside wq#4569's stated scope
and does not make the shipped fix incomplete. Filed as a separate follow-up, wq#4590.

## Why this is a judgment pass

The wq#4569 author and the wq#4587 verifier are both claude-sonnet-5. The verifier
(me, sonnet-5@high) found a live, reproducible false-block that the PR left in place,
which is grounds for a contradiction verdict on a narrower reading. Per fleet protocol
that escalates to an opus-5@high tie-break before the verify-pass commits to CONTRADICT.
Opus independently reproduced the finding and ruled CONFIRM.

## What wq#4569 shipped

PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2427, commit `f539e18dc5`
(merged `b43a56dd`). harness/guard.sh's never-public-ccc push-block wall matches two
OR'd arms against the raw command text: a GitHub org/repo literal and a filesystem-path
literal. The GitHub arm was `grep -qiE 'RealLumenHere/ccc\b'` — a bare `\b` fires at
any non-word char, so it both missed case variance (fixed earlier, wq#4568) and
over-matched: it false-blocked an unrelated repo whose name merely starts with the
protected name followed by a hyphen (e.g. a hypothetical `ccc-mirror` repo). wq#4569
tightened it to require `.git`, whitespace, or end-of-string right after the name,
added MUST_ALLOW corpus cases for the hyphenated false-positive (both case variants)
and MUST_BLOCK cases for the case-insensitive/EOL forms it still must catch. All 81
guard tests pass. ISSUE-LOG rows 1533 (original finding, wq#4566) and 1556 (this fix)
describe this scope precisely — "the repo name" / "GitHub repo slugs" — and nothing else.

## What the verifier found

The filesystem-path arm, one line below the line wq#4569 edited, still reads
`grep -qE '/home/lumen/ccc\b'` — the exact same bare-`\b` mechanism, untouched. No
MUST_ALLOW corpus case covers a sibling-named path. An adjacent comment (added by
wq#4568, not wq#4569) asserts widening this match "would only block paths that cannot
exist" — false: a sibling directory sitting next to the protected tree, named with the
same prefix plus a hyphen or dot, is an ordinary path that can exist.

Reproduced live, twice, independently:

1. Verifier's own Bash tool call in-session (not even a real push — a `printf | grep`
   string test mentioning a sibling path) was blocked by the live guard hook itself,
   stderr: `guard.sh: BLOCKED — git push toward RealLumenHere/ccc denied
   (wq#4550/wq#4551/wq#4568: public mirror of never-public ccc; cwd=...)`.
2. Verifier's `/tmp/fworker-wq4587/probe_fsarm.py`, feeding guard.sh the same hook-JSON
   envelope `harness/tests/test_guard.py`'s `run_guard` helper uses: a benign
   `git -C <sibling-path> push origin main` and a comment-only mention of a different
   sibling path were both wrongly blocked (rc=2); a push of the real protected tree
   correctly still blocked (control).
3. Opus's independent `/tmp/fworker-wq4587/judge_probe.py` added more controls —
   confirmed the GitHub-side fix works (a `ccc-mirror.git`-style GitHub push now
   allowed, rc=0), confirmed the real tree and its subdirectories still block (rc=2),
   and confirmed a sibling path whose suffix starts with a word character (no boundary
   fires) passes while one starting with `-` or `.` is false-blocked (rc=2) — pinning
   the mechanism exactly to the bare-`\b` class wq#4569's own commit message diagnoses.
4. `grep -n 'lumen/ccc' harness/tests/test_guard.py` (opus) found only exact-tree
   references (lines 92, 861, 877, 903) — no sibling-path case exists anywhere in the
   corpus for either arm.
5. Even filing the wq#4590 follow-up ticket itself tripped the live bug: the first
   `wq add` attempt, whose `--detail` text named the protected path next to the words
   "git"/"push" in a quoted regex, was blocked by this exact guard rule. Retyped the
   ticket text to describe the pattern without the literal triggering substring.

## Scope judgment

wq#4569's title, ticket detail, and both referenced ISSUE-LOG rows describe the bug as
being about "the repo name" / "GitHub repo slugs" — the org/repo literal specifically.
The filesystem-path literal is a different string serving a different purpose (it also
has to keep blocking the protected tree's own subdirectories, which the GitHub arm does
not need to consider), so fixing it is not a mechanical copy of wq#4569's patch — it
needs its own boundary design and its own corpus cases. The wq#4568 comment's incorrect
claim is itself from before wq#4569's diff (unchanged by it) and was never flagged by
any prior verify-pass (rows 1533/1556 are silent on it). Treat this as a separate,
pre-existing, now-discovered defect rather than an unfinished piece of wq#4569's work.
It also fails closed — worst case is an annoying false block, never a leak of the
protected tree — so there is no urgency pressure toward reopening wq#4569 to cover it.

## Disposition

- wq#4569: closure stands (CONFIRM). Noted on the ticket (`wq note 4569 ...`).
- ISSUE-LOG row 1560: new row logging this finding, status "open (follow-up ticket
  pending)".
- Follow-up ticket wq#4590 filed (lane build, pri 4, effort medium): tighten the
  filesystem-arm pattern to require a path separator/quote/whitespace/`;`/EOL after the
  tree name, add MUST_ALLOW corpus cases for sibling-named paths and MUST_BLOCK cases
  for the tree and its subdirectories, and correct the inaccurate wq#4568 comment.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
