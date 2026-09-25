# wq#2680 — verify pass: wq#2671's fix to rollback.sh's DC_UNIT restore branch

**Verdict: CONFIRM. All three items hold. wq#2671's fix (PR#1049, merged `baa10265d0`)
is correct, its committed regression test is load-bearing (not decorative), and its
retraction of ISSUE-LOG row 766's proposed remedy is itself correct against the actual
scripts. wq#2671's ruling that wq#2608's `done` stands is consistent with the
wq#2630/wq#2290 closure-quality precedent.**

Fresh instance, no memory of wq#2646/wq#2671's sessions. Re-derived from primary
sources: the four scripts in `ops/wq2608-dc-endpoint-bes-a1/`, the committed test, git
history, and `wq show` on 2608/2671/2680. Not re-implementing per verify-pass doctrine.

## 1. DC_UNIT restore block — CONFIRMED correct

`ops/wq2608-dc-endpoint-bes-a1/rollback.sh:23-29`:

```
if [ -e "${DC_UNIT}.pre-wq2608.bak" ]; then
  cp -a "${DC_UNIT}.pre-wq2608.bak" "$DC_UNIT"          # restore
else
  rm -f "$DC_UNIT"                                       # remove (net-new)
fi
```

Cross-checked against `apply.sh`: `backup()` (`apply.sh:59-65`) returns early
(`[ -e "$path" ] || return 0`) when the path does not already exist — it never writes a
`.bak` for a net-new install — and `install -m 0644 "$HERE/dc-gateway.service" "$DC_UNIT"`
(`apply.sh:68`) runs unconditionally, with no existence guard. So "no backup" can only
mean apply.sh installed the unit net-new, and removal is the only correct rollback.
Matches the ticket's description exactly.

## 2. Regression test is load-bearing, not decorative — CONFIRMED

Ran `test_rollback_restore_branches.py` as committed:

```
pre-fix blob @ 2681073: DC_UNIT backup-exists -> file-absent (MISHANDLED, want restored)
                         DC_UNIT no-backup     -> live-left-in-place (MISHANDLED, want removed)
                         CFD_CONFIG both states -> ok
current rollback.sh:    all four states -> ok
PASS
```

To confirm the witness half (`want_correct=False` branch, `test_rollback_restore_branches.py:116-123`)
actually asserts something rather than trivially passing, I copied the test to
`/tmp/fworker-wq2680/test_inverted.py` and replaced `pre_fix_source()` — which normally
does `git show 2681073:.../rollback.sh` — with a version that returns the *current,
already-fixed* `rollback.sh` instead. If the witness assertion is load-bearing, feeding
it non-buggy source must flip the result from PASS to FAIL. It did:

```
pre-fix blob @ 2681073 (regression witness -- DC_UNIT must fail both states):
  DC_UNIT backup exists -> restored-from-backup (want restored-from-backup) ok
  DC_UNIT no backup     -> file-absent          (want file-absent)          ok
  ...
FAIL: pre-fix blob was expected to mishandle both DC_UNIT states and neither
CFD_CONFIG state; got []. The test harness, not the script, is probably wrong.
EXIT=1
```

This is exactly what "the test can actually fail" means, applied to the half the ticket
called out. (The 2671 report separately notes the CFD_CONFIG *control* caught a bug in
an earlier draft of the fix — a second, independent load-bearing signal from the same
harness.)

## 3a. Asymmetry claim — CHECKED against the actual scripts, CONFIRMED correct

wq#2671 retracted "mirror the CFD_CONFIG block" on the grounds that `DC_UNIT` and
`CFD_CONFIG` are asymmetric. Verified both halves directly:

- **DC_UNIT**: per §1 above — `backup()` only snapshots pre-existing paths, `apply.sh`
  installs the unit unconditionally. No backup ⇒ net-new ⇒ must remove. Confirmed by
  reading `apply.sh:59-68`.
- **CFD_CONFIG**: `apply.sh:30-34` REFUSES outright if `$CFD_CONFIG` does not already
  exist, *before* the patch script ever runs. `cloudflared_ingress_patch.py:39-41`
  independently REFUSES if the config path doesn't exist, and unconditionally writes
  `<config>.pre-wq2608.bak` the first time it runs against an existing config
  (`cloudflared_ingress_patch.py:43-46`, no existence guard on the write beyond "config
  already existed to get this far"). So for any successful `apply.sh` run, a CFD_CONFIG
  backup is guaranteed. The no-backup `else` there really is edge-case/tamper territory,
  where warn-and-leave is the safer default than deleting an operator's live tunnel
  config.

The asymmetry claim holds against the code as written, not just as described.

## 3b. wq#2608 closure ruling vs. wq#2630/wq#2290 precedent — CONFIRMED consistent

wq#2630 (`reports/2630_judgment_wq2290_closure.md`) drew the controlling distinction:
a closure stands, uncorrected, when the ticket's bar was met and demonstrated and its
notes make no false evidentiary claim; an *overclaim* (a probe result cited as if it
covered a population it didn't) gets a correcting `wq note`, not a reopen; a systemic
lesson about probe/closure shape is recorded as **doctrine, not a new `wq
closure-quality` metric**, because that metric mechanically reads artifact-landedness
and cannot see judgment-shaped gaps.

wq#2671 applied the identical structure to wq#2608:

- wq#2608's notes (`wq show 2608`) say verbatim: *"Proposal only: this BUILD worker has
  zero fleet-exec grant to Bes... README.md has exact remaining commands."* This is not
  an overclaim — it makes no claim that rollback.sh was exercised at all, so there is
  nothing analogous to wq#2290's "35 tracked files page before, 0 after" sentence to
  correct. Closure stands *more* cleanly than wq#2290's did.
- A correcting note was appended rather than a reopen — confirmed live: `wq show 2608`
  now contains a `wq#2671` reference and a `RESOLVED`/`CONTRADICT upheld` entry (2 hits
  each via `grep -c`), matching wq#2630 §4's "correcting wq notes appended... neither
  reopened" pattern.
- The systemic lesson (ISSUE-LOG #776: "no grant to deploy is not no way to test") was
  recorded as a doctrine one-liner in `ISSUE-LOG.md`, explicitly citing "wq#2630
  precedent: no new metric" (`reports/2671_judgment_wq2608_closure.md:116-117`) rather
  than proposing a `wq closure-quality` change. Same instrument choice wq#2630 made for
  the confirmatory-probe lesson.

No inconsistency found between the two rulings.

## Liveness

`wq show 2671` → `status: done`. Fix commit `baa10265d0` present in this worktree's
history (`git log --oneline -1 baa10265d0` → `fix(ops): correct rollback.sh dc_unit
restore branch`).

## Summary for the chain

| item | verdict |
| --- | --- |
| (1) DC_UNIT restore block correctness | CONFIRMED |
| (2) regression test load-bearing (witness half) | CONFIRMED — fails when fed fixed source |
| (3) asymmetry claim vs. actual scripts | CONFIRMED |
| (3) wq#2608 closure ruling vs. wq#2630 precedent | CONFIRMED consistent |

No CONTRADICT. Nothing re-implemented. Mutation artifact left at
`/tmp/fworker-wq2680/test_inverted.py` (scratch, not committed).
