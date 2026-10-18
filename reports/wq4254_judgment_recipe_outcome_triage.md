# wq#4254 — judgment on wq#4245's CONTRADICT of wq#4235

**Ruling: contradiction UPHELD, landed as a fast-follow. wq#4235 stays closed.**

Judge: claude-opus-5@high, worktree `wq4254-a1`. Input: wq#4245's verify report
(`/home/lumen/worktrees/wq4245-a1/reports/wq4245_verify_wq4235_recipe_crystallizer_real_block.md`),
ISSUE-LOG row 1425, and a cold read of the landed code.

## 1. Is the defect real?

Yes, and it is not a judgment call — it is visible in the source and reproduces.
`handle_failure()` scored every terminal block against the recipe cell:

```
4714: log(f"BLOCKED [{block_class}/{rec['triage']}]: {reason}")
4715: record_recipe_outcome(ticket, args, success=False)      # no triage reference
4716: if rec["triage"] != failure_record.TRIAGE_REAL_BLOCK:   # one line below: gated
```

Independently re-reproduced (not a re-run of wq#4245's script):
`/tmp/fworker-wq4254/probe_triage_gate.py` drives the real `handle_failure()` against
a real `recipe_crystallizer` store on a scratch path, with only the wq/ntfy/
terminal-record seams stubbed, and runs the identical scenario with the exemption set
emptied (pre-fix) and as shipped:

```
pre-fix (exemption set emptied)    triage=REAL_BLOCK    frozen before=True after=False
as shipped                         triage=REAL_BLOCK    frozen before=True after=True
as shipped, competence signal      triage=GENUINE_BUG   frozen before=True after=False
PROBE OK: the gate, not the test, is what makes the exempt cases pass
```

The third line is the falsifier. A "fix" that just stopped recording failures would
pass the first two and fail the third.

## 2. Reopen wq#4235, or fast-follow?

**Fast-follow.** The reasoning, in the order it decided the call:

1. **Nothing wq#4235 actually shipped is wrong.** wq#4245 read
   `recipe_crystallizer.py`'s freeze/unfreeze/exclusivity logic and
   `foreman_dispatcher.candidate_model()`'s routing and found both correct; I spot-read
   the same seams and agree. The defect lives in one line of `harness/fworker.py` — the
   *caller*. Reopening wq#4235 would redispatch a ticket whose deliverable is sound.
2. **The blast radius points the safe way.** An unfrozen recipe is not a wrong dispatch;
   it is the absence of a shortcut. `candidate_model()` falls back to `router.assign()`,
   i.e. exactly the pre-wq#4235 behaviour. The failure mode is "crystallization never
   happens on ticket classes where blocks are common" — a feature that quietly does
   nothing, not a feature that does damage. That is a medium, not a pri-1.
3. **A reopen keeps the defect live longer.** Redispatch, re-review and re-land is days;
   the fix is one gate plus tests, and the defect sits in `main` the whole time either
   way. There is no scenario where waiting is the safer option.
4. **The fix is independently testable**, which is the textbook fast-follow shape: it
   needs no change to wq#4235's module, its tests, or its dispatch wiring.

## 3. The fix

`harness/failure_record.py:53-64` — `TRIAGE_NON_COMPETENCE = {REAL_BLOCK,
CAPABILITY_DENIED, HARNESS_DEFECT}`, defined next to the triage constants because it is
a statement about what those classes mean, not about fworker.

`harness/fworker.py:807-824` — `record_recipe_outcome()` takes `triage` and returns
early at line 822 (recording **nothing** — not a loss, not a win) when it is in that
set. `harness/fworker.py:4727` passes `rec["triage"]`; the success call site
(`fworker.py:4389`) passes nothing and keeps counting, as before.

Why the gate lives in the callee rather than as an `if` around the call site: it makes
the policy unit-testable without standing up `handle_failure()`, and it means a future
third call site cannot forget it.

**What stays IN.** `TRIAGE_LIMIT` (a TURNS/BUDGET ceiling) and `TRIAGE_UNKNOWN` (no
mechanical signature matched) remain recorded as losses. Burning the ceiling without
landing the ticket is a competence claim about the cell, and an unexplained terminal
failure after `MAX_ATTEMPTS` is the case where you *want* the recipe to stop pinning
itself. The exemption is for blocks whose cause is provably not the model — a
human-decision stop, a sandbox wall, or our own harness bug — not for blocks that are
merely unflattering.

**The error this fix introduces, bounded.** It biases toward keeping a recipe pinned.
That is tolerable because the exempt classes all describe tickets where the recipe never
got to be wrong: a `REAL_BLOCK` ticket stopped before producing a diff, a
`CAPABILITY_DENIED` one hit a wall any model hits, and a `HARNESS_DEFECT` one never ran
in a valid worktree. Every class that reflects work the model actually did and got wrong
still unfreezes on the first failure, with no grace window.

## 4. Coverage

`harness/tests/test_recipe_outcome_triage.py` (12 tests, all new; `12 passed`). It drives
the real `handle_failure()` against a real store: the three exempt classes leave the
frozen recipe *and* the cell counters byte-identical; `GENUINE_BUG`/`LIMIT`/`UNKNOWN`
still unfreeze and still zero the streak; an untracked ticket class writes nothing; the
success path still counts with no `triage` argument; and a `REAL_BLOCK` between two wins
no longer prevents the third win from freezing. Before this file, `grep -rn
record_recipe_outcome harness/tests/` had zero hits.

Regression run: `dispatcher/tests/test_recipe_crystallizer.py`,
`harness/tests/test_local_rung_escalation.py`, `test_failure_record.py`,
`test_charging.py` plus the new file — `111 passed`.

## 5. wq#4245's secondary note

Upheld as a *documentation* defect and fixed as one (`recipe_crystallizer.py:23-31`).
The docstring claimed an atomic tmp+rename; `_save_locked()` truncates and writes in
place. The code is right and the docstring was wrong: a rename swaps the inode out from
under every other holder of the `flock` taken on that file, so adopting tmp+rename here
would silently stop the lock from serialising concurrent `record_outcome()` calls across
worktrees — the one thing it exists to do. Docstring now says what the code does and why
it differs from `foreman_dispatcher.save_state()`.
