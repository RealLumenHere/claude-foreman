# wq#2817 — judgment: wq#2763 vs wq#2740, kb ISSUE-LOG row 824's pointer

Worker: opus-5@high, worktree `/home/lumen/worktrees/wq2817-a1`, 2026-09-28.
Subject: wq#2763's contradiction of wq#2740 ruling 2, and the landing path for the fix.

## Decision table

| # | question | ruling |
|---|---|---|
| 1 | Is wq#2763 right that row 824 points at the wrong row? | **Yes.** Confirmed at the committed blob, not just the working tree. |
| 2 | Is row 835 the correct target (not 836)? | **Yes.** 835 is wq#2740's judgment on wq#2736's contradiction of wq#2733 — the row that supersedes 824's specifics. 836 is the unrelated five-failing-tests finding from the same run. |
| 3 | Is the ticket's "no git-committable path for kb" premise still true? | **No — stale.** wq#2759 closed 2026-09-26 06:04; the Edit is now committed automatically. |
| 4 | Is wq#2740's substance wrong, or only its self-description? | **Only its self-description.** Ruling 2's intent (point row 824 at the judgment that overturns it) was right; it typed the wrong row number into both the kb cell and its own report. |

## Evidence for ruling 1

- wq#2740's report claims the outcome: `reports/2740_judgment_wq2736_wq2733_rotation_a745825001bc.md:13`
  — "row 824's status cell now points at 835".
- What its kb commit actually wrote: `15f63c8` ("kb: ISSUE-LOG rows 835/836 — correct row 824
  (wq#2740 judgment)") left row 824's status cell reading `see row 834`.
- Still live 2 days later, in the **committed** blob, not only the dirty working tree:
  `git -C /home/lumen/kb show 10e77b2:ISSUE-LOG.md | grep '^| 824 '` → `see row 834`.
- Row 834 at that rev (L958) is wq#2739's judgment on wq#2737's contradiction of wq#2731 —
  a different chain. Row 835 (L960) is the wq2736/wq2740/wq2733 chain, and it cites
  "row 824 above closes rotation row cloudflare `sha256:a745825001bc` on evidence that does
  not exist", i.e. it is the row a reader of 824 needs.

Rulings 1 and 3 of wq#2740 (the `a745825001bc` non-credential finding and the `account()`
prefix-adjacency fix at `harness/mask_replay.py:428`) are untouched by this — wq#2763 already
confirmed them against merged main, and this ticket found no reason to revisit them.

## Evidence for ruling 3 (the premise the ticket got wrong)

`bin/session-handoff:24-34` documents the wq#2759 fix and `:253` implements it:
`git_commit([OUT_PATH, NOTES_PATH, ISSUE_LOG_PATH])`. `session-handoff.timer` runs every
30 min as a trusted, non-sandboxed service. Combined with `harness/guard.sh:503` (wq#1831,
Edit on `/home/lumen/kb/ISSUE-LOG.md` is the one sanctioned in-place primary-tree write),
a worker needs no commit verb and kb needs no origin remote.

Demonstrated end to end on this ticket: the Edit was made at 03:2x UTC, the timer fired at
`2026-09-28T03:26:15Z`, and `b71131d` carries both changes — `git -C /home/lumen/kb show
b71131d:ISSUE-LOG.md | grep '^| 824 '` → `see row 835`, and row 870 is present. No manual
sweep, no blocked command. **wq#2817 is therefore not a second demonstration of wq#2759's
gap; it is the first demonstration that the gap is closed.**

## Fix applied

`kb/ISSUE-LOG.md` row 824 status cell: `see row 834` → `see row 835`, plus a note naming the
old wrong target so the correction is auditable from the row itself. New row 870 records the
issue and this solution. Landed in kb `b71131d`.

## Probe — generalised so it can fail outside the fixed row (wq#2630)

A one-row edit verified by re-reading that one row is a probe that cannot fail. So the finding
was generalised into `ops/wq2817-issue-log-pointer-integrity/probe_pointers.py`, which checks
**every** row of the log for:

- `DUP` — a row number defined more than once (any `row N` citation of it is ambiguous),
- `DANGLING` — a `see row N` whose target number does not exist,
- `ONE-WAY` — a `see row N` where row N never cites the citing row back. Correction rows in
  this log always name their subject, so a missing back-reference is the signature of exactly
  the mis-typed target wq#2740 produced.

Input is rev-pinnable (`--rev <sha>`), so runs are reproducible after the live file moves.

```
--rev 10e77b2 (pinned baseline, bug live)   827→819 rows   FAIL (124)
  ONE-WAY  row 824 (L948) -> 'see row 834': row 834 never mentions row 824   <- the defect
after the edit (working tree)                              FAIL (124)
  that row-824 line is gone; the only added line is DUP row 869, a collision
  two other lanes (wq#2787, wq#2836) created concurrently while this ran
```

It fails on rows this fix never touched — 122 pre-existing duplicate row numbers (including
two rows numbered 836, written by wq#2740's own commit next to wq#1789's) and one more
one-way pointer, row 164 → row 38. Those are pre-existing and left OPEN, filed as a
follow-up rather than renumbered here: renumbering rows in an append-only log would break any
citation of the old number, and nothing cites 836 today (`grep -c 'row 836'` → 0).

Checker logic has committed-fixture regression cover in
`harness/tests/test_wq2817_issue_log_pointers.py` (5 tests, all passing) — synthetic three-row
tables reproducing wq#2740's shape, so the tests do not read the live kb file.

## Bottom line

wq#2763 was right, and its own bottom line — citation hygiene — is what wq#2740 failed on.
The remedy is not more care: it is the checker, because "the row number I typed points at the
row I meant" is not something a self-reported decision table can verify about itself.
