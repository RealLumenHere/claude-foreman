# wq#1486 — verify-pass of wq#1485 (docs: record research_prompts index in BROKER_GRANT_COVERAGE.md)

**Verdict: CONFIRMED.** PR#344 (merged, commit `e86fd38` rewritten to `3c26f42` on
`origin/main`, byte-identical diff) does exactly what wq#1485 was scoped to do: append
one templated row to `research/BROKER_GRANT_COVERAGE.md` §4 documenting the live drill
proof-i from §5 slice 3, and repoint `evidence.json`'s AC1 citation at it. No scope creep,
no fabricated claim.

## What was claimed (wq#1485 / PR#344)

- Ticket text (`wq show 1485`) specified an exact template: read
  `/home/lumen/research_prompts/RP_fleet_autonomy_stepback_2026-07-12.md` line 1 only
  (an allow-listed dir under `needs:dir`), then append verbatim:
  `| i (live) | worker read an allow-listed dir via needs:dir and landed this line | PASS <date> wq#<id> |`
  under §4, and nothing else.
- `wq show 1485` notes: PR#344 merged, added row i (live) at
  `research/BROKER_GRANT_COVERAGE.md:107`.

## Independent verification

- Fetched `refs/pull/344/head` into a local branch. The wq#1485 commit (`e86fd38`) touches
  exactly two files: `evidence.json` and `research/BROKER_GRANT_COVERAGE.md` — matches the
  ticket's "No other changes" instruction. The appended row is a character-for-character
  match to the template with `2026-09-08` / `wq#1485` filled in.
- Confirmed `e86fd38` is content-identical to `origin/main`'s `3c26f42` (`git diff e86fd38
  3c26f42` — empty; only commit metadata differs, a gitea rebase-merge trailer addition).
  The PR is genuinely merged, not sitting open.
- `evidence.json`'s new citation (`research/BROKER_GRANT_COVERAGE.md:107`) is accurate —
  read line 107 of the merged file directly; it is the new row, not an adjacent line.
- Cross-checked the row's own claim against the policy it describes: `/home/lumen/research_prompts`
  is genuinely present in `harness/fleet-exec-policy.yaml:71` under `allow.read_dirs`
  (landed by the preceding commit `57491d3`, §3's sweep table: 74 files, 0 hits, "on
  allow? yes"), and `harness/capability_lint.py`'s `allow_hint`/read-dir-tag logic
  (`allow_section`, the `dir(X) is not under allow.read_dirs` unresolved-path) implements
  the described allow-hit tagging in code, not just in prose. The ticket's own `needs`
  field (`dir(/home/lumen/research_prompts)`) is consistent with that mechanism.
- Ran every test suite the doc's §4 table cites as evidence for the surrounding slice-1
  work this row depends on, from a disposable `git worktree add` off `pr-344` (removed
  after): `harness/tests/test_capability_allow.py` + `test_read_dir_sweep.py` +
  `test_capability_lint.py` → **114 passed**, matching the doc's stated 20+15+79. The
  three touched `dispatcher/tests/` files (`test_capability_replay.py`,
  `test_dispatcher.py`, `test_runaway.py`) → **127 passed**.
- `wq show 1485` — `status: done`, notes cite PR#344 merged with the same file:line as
  observed directly.

## Adversarial checks that did NOT find a break

- Checked whether the PR's bundled diff (12 files, 3 commits: `a87dd57` wq#1475 verify,
  `57491d3` feat/lint, `e86fd38` wq#1485 docs) meant wq#1485's worker made unauthorized
  changes beyond its ticket. It didn't — `a87dd57` and `57491d3` were already committed to
  the shared lane branch (timestamps 2026-09-07T22:53:56Z and 2026-09-08T05:40:53Z,
  both before wq#1485 claimed at 05:43:17Z) and rode along in the same PR merge; isolating
  `e86fd38`'s own diff shows only the two files described above.
- Checked the row's self-referential claim ("landed this line") isn't circular padding
  masking a missing mechanism: independently confirmed the allow-list entry and lint code
  exist (previous bullet) rather than trusting the doc's own prose.
- Could not independently re-read
  `/home/lumen/research_prompts/RP_fleet_autonomy_stepback_2026-07-12.md` myself — that
  directory isn't in this verify ticket's granted paths, which is correct scoping, not a
  gap in the original worker's evidence. Neither the commit message, PR notes, nor
  `evidence.json` cites that file's line 1 by content; the ticket's "cite it" most plausibly
  refers to the read having actually happened (attested by the `needs:dir` grant + the
  fixed-template row landing), not a second inline citation — the deliverable template
  itself has no slot for one. Flagging as a non-blocking wording ambiguity, not a defect.

## Conclusion

No contradiction found. The diff matches the ticket's exact template, the citation is
accurate post-merge, the allow-list and lint mechanism the row's claim depends on are real
(not just documented), and every test suite the surrounding work cites passes at the
merged tip. Board stays CONFIRMED — no escalation to opus-5@high.
