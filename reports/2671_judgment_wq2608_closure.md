# wq#2671 — judgment: wq#2646's CONTRADICT of wq#2608 (rollback.sh DC_UNIT branch swap)

**Verdict, one line: the CONTRADICT stands and I reproduced it independently;
wq#2608's `done` stays `done` (its notes were honestly scoped and claimed nothing
about rollback.sh being exercised), and the fix is landed here rather than
deferred — but *not* the fix wq#2646 proposed, because "mirror the CFD_CONFIG
block" would have left one of the two bugs in place.**

Ticket asked for two rulings: (1) revise or flag wq#2608's closure, (2) authorize a
follow-up fix ticket. Answers below, plus a third thing the re-verification turned up
that neither wq#2646 nor the ticket had: the proposed remedy was wrong.

## 0. What I re-verified independently

`ops/wq2608-dc-endpoint-bes-a1/test_rollback_restore_branches.py` (new, committed).
It extracts each `if [ -e "${VAR}.pre-wq2608.bak" ]` block verbatim from rollback.sh,
runs it against throwaway files in a temp dir, and asserts the resulting on-disk state
for both reachable backup states. No root, no systemd, no Bes.

It runs over two revisions: the pre-fix blob pinned at `2681073` and the current file.
The pinned blob is the regression witness — it must still mishandle both `DC_UNIT`
states, which is what proves the test can detect the bug at all. Output:

```
pre-fix blob @ 2681073 (regression witness -- DC_UNIT must fail both states):
  DC_UNIT    backup exists -> file-absent          (want restored-from-backup) MISHANDLED
  DC_UNIT    no backup  -> live-left-in-place   (want file-absent         ) MISHANDLED
  CFD_CONFIG backup exists -> restored-from-backup (want restored-from-backup) ok
  CFD_CONFIG no backup  -> live-left-in-place   (want live-left-in-place  ) ok

current rollback.sh (must handle all four):
  DC_UNIT    backup exists -> restored-from-backup (want restored-from-backup) ok
  DC_UNIT    no backup  -> file-absent          (want file-absent         ) ok
  CFD_CONFIG backup exists -> restored-from-backup (want restored-from-backup) ok
  CFD_CONFIG no backup  -> live-left-in-place   (want live-left-in-place  ) ok
```

wq#2646's finding is confirmed exactly as stated: both of `DC_UNIT`'s only two
reachable states are mishandled, so the block is wrong on 100% of invocations. On the
near-certain first-deploy path it prints a removal message and removes nothing
(`apply.sh:59-66`'s `backup()` returns early when the path does not exist, so a
net-new unit never gets a `.bak`); on the backup-exists path it deletes the live unit
and leaves the backup it was written to restore sitting orphaned next to it.

**The probe could fail, and on its first run it did.** My initial version asserted the
same expectation for both blocks — "no backup means net-new, so remove it" — and the
`CFD_CONFIG` control came back MISHANDLED, which under the harness's own rule meant
*the probe, not the code, is wrong*. That was correct: see §2. The control is what
caught it, which is the whole reason a probe needs one.

## 1. Should wq#2608's `done` be revised? — **No. Flag it, don't reopen it.**

wq#2608's done-notes say, in their own words: *"Proposal only: this BUILD worker has
zero fleet-exec grant to Bes (policy allows ssh(bes)/copy(bes), ticket's capability
grant has neither -- confirmed live, ISSUE-LOG #742) and no root verb regardless.
README.md has exact remaining commands."*

That is an accurate description of what shipped. The closure claimed no live
deployment, no rollback drill, and no root access it did not have. Contrast the
closure defect wq#2630 ruled on, where the done-notes asserted a specific evidentiary
fact ("35 tracked files page…") that did not hold — **there is no false claim here to
revise.** The bundle's exercisable pieces were independently checked by wq#2646 and
are sound.

Reopening would also buy nothing: it would not remove the defect, it would cost a full
worker redispatch on four lines of shell, and it would misattribute the failure. The
defect is in the one artifact in the bundle that *no* gate available to wq#2608 could
have caught by inspection-plus-grants — which is the actual lesson, in §3.

So: closure stands; wq#2608's note gets this judgment's outcome and the fix commit
appended, so nobody reading that ticket runs rollback.sh believing its gate covered it.

## 2. Authorize a follow-up fix ticket? — **Authorized, and executed here instead.**

The remaining work was fully diagnosed before this ticket opened, the change is four
lines, and a re-runnable exercise for it already existed by the time I finished
verifying. Filing a ticket to hand that to a fresh instance would burn a session to
re-derive what is written above. Full autonomy says decide and act, so it is landed.

Independence is preserved where it actually matters: wq#2646 (the verifier) correctly
declined to re-implement, and I am ruling on wq#2608's *closure*, not blessing my own
verify pass. A fresh-instance verify ticket on this fix is filed.

**The important part: wq#2646's proposed remedy was wrong, and so is ISSUE-LOG row
766's.** Both say to fix `DC_UNIT` by mirroring the `CFD_CONFIG` block immediately
below it. Mirroring it verbatim fixes the backup-exists case and leaves the
no-backup case broken, because the two blocks are **not symmetric**:

| | no-backup state means | correct behaviour |
|---|---|---|
| `DC_UNIT` | unit is net-new (`backup()` only snapshots pre-existing paths; `apply.sh:68` installs unconditionally) | **remove it** |
| `CFD_CONFIG` | unreachable in practice — `cloudflared_ingress_patch.py:43-46` always writes the backup, since config.yml must exist for cloudflared to be running | **warn, leave it** — deleting an operator's cloudflared config is far worse than a lingering ingress entry |

`CFD_CONFIG`'s warn-only `else` is correct *for `CFD_CONFIG`*. Copied onto `DC_UNIT` it
reproduces the exact first-deploy failure wq#2646 found: rollback announces success and
leaves the unit installed. The landed fix swaps the bodies **and** makes the `else`
remove, with a comment at the site recording why the two blocks must differ so the next
reader does not "tidy" them back into symmetry.

This is what the second look was worth. A fix ticket dispatched against the remedy as
written would have shipped a half-fix that still fails the near-certain real path.

## 3. The pattern worth carrying forward

Two consecutive CONTRADICTs now (wq#2290/wq#2355, wq#2608/wq#2646) landed on an
artifact the closing worker had no grant to execute. That is the signal: **not
dishonest closures — unexercisable ones.** wq#2608 could not run rollback.sh against
Bes and said so plainly; the bug then shipped because "cannot run it for real" was
silently treated as "cannot exercise it at all."

It isn't. wq#2646 reproduced both mis-states against fake paths in minutes with no
root, and the committed test here does the same in about 80 lines. Extracting a shell
block and running it against a temp dir needs none of the privileges the real target
does.

Per wq#2630's precedent I am **not** proposing a new metric — `wq closure-quality`
cannot see this and should not be taught to. The rule belongs where workers already
read, as a one-liner: *a script you cannot run against the real target still gets a
logic exercise against fake paths before you close — "no grant to deploy" is not "no
way to test."* Proposed for instincts via the normal proposal-only path; not
self-applied.

## Summary for the chain

- CONTRADICT **upheld**, independently reproduced, pinned at `2681073`.
- wq#2608 `done` **stands** — honestly scoped, no false evidentiary claim; note flagged.
- Fix **landed here**, not deferred: `rollback.sh` DC_UNIT bodies swapped *and* the
  `else` made to remove.
- wq#2646's and ISSUE-LOG #766's "mirror the CFD_CONFIG block" remedy is **wrong** —
  it would have left the first-deploy bug in place. Row 766 corrected.
- New committed exercise: `test_rollback_restore_branches.py`, with the pre-fix blob as
  a regression witness so it cannot silently stop detecting the bug.
- Fresh-instance verify ticket filed on the fix.
