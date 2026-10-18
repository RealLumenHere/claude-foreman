# wq#4254 — judgment: exempt non-competence triage classes from recipe outcomes

## What

`handle_failure()` no longer scores a terminal block against the
`(ticket_type, model, prompt_template)` recipe cell when the block's triage class says
nothing about model competence. Also carries the judgment ruling on wq#4245's
CONTRADICT of wq#4235, and a docstring correction wq#4245 flagged as a secondary note.

## Why

wq#4245's verify-pass CONTRADICTed wq#4235. `handle_failure()` scored **every**
terminal block as a recipe loss:

```
4714: log(f"BLOCKED [{block_class}/{rec['triage']}]: {reason}")
4715: record_recipe_outcome(ticket, args, success=False)      # no triage reference
4716: if rec["triage"] != failure_record.TRIAGE_REAL_BLOCK:   # one line below: gated
```

So a `REAL_BLOCK` — the worker correctly recognising a Frank-only decision and
self-blocking, the documented good path — unfroze a working recipe, as did a
`CAPABILITY_DENIED` (sandbox wall no model gets past) and a `HARNESS_DEFECT`
(worktree/repo mismatch, our bug). None of the three says anything about whether that
`(model, prompt_template)` is any good at that ticket class.

**The ruling: fast-follow, not a reopen of wq#4235.** `recipe_crystallizer.py` and
`foreman_dispatcher.candidate_model()` are correct — the defect is one line in the
*caller*, in a different file. The blast radius also points the safe way: an unfrozen
recipe is the absence of a shortcut, so `candidate_model()` falls back to
`router.assign()`, i.e. pre-wq#4235 behaviour. Nothing mis-dispatches and nothing is
lost; crystallization just never happens on ticket classes where blocks are common.
That is a medium, and redispatching a sound ticket would leave the defect live in
`main` for days longer than fixing it here does. Full reasoning:
`reports/wq4254_judgment_recipe_outcome_triage.md`.

## How

- `harness/failure_record.py:63-64` — `TRIAGE_NON_COMPETENCE = {REAL_BLOCK,
  CAPABILITY_DENIED, HARNESS_DEFECT}`, next to the triage constants because it is a
  statement about what those classes mean, not about fworker.
- `harness/fworker.py:822-824` — `record_recipe_outcome()` takes `triage` and records
  **nothing** for those classes (not a loss, not a win). Gating in the callee rather
  than around the call site keeps the policy unit-testable without standing up
  `handle_failure()`, and means a third call site cannot forget it.
- `harness/fworker.py:4727` — the block call site passes `rec["triage"]`; the success
  site (`:4389`) passes nothing and keeps counting.

`LIMIT` (TURNS/BUDGET ceiling) and `UNKNOWN` deliberately stay **in**: burning the
ceiling without landing the ticket is a competence claim about the cell, and an
unexplained terminal failure after `MAX_ATTEMPTS` is precisely the case where a recipe
should stop pinning itself. The exemption is for blocks whose cause is provably not the model,
not for blocks that are merely unflattering.

wq#4245's secondary note is upheld as a *documentation* defect and fixed as one
(`dispatcher/recipe_crystallizer.py:23-31`): the docstring claimed an atomic
tmp+rename, `_save_locked()` truncates in place. The code is right — a rename swaps the
inode out from under every other holder of the `flock`, which would stop the lock
serialising concurrent `record_outcome()` calls across worktrees.

## Testing

`harness/tests/test_recipe_outcome_triage.py` — 12 new tests driving the real
`handle_failure()` against a real `recipe_crystallizer` store on a scratch path (only
wq/ntfy/terminal-record seams stubbed). The exempt classes leave the frozen recipe and
the cell counters byte-identical; `GENUINE_BUG`/`LIMIT`/`UNKNOWN` still unfreeze and
zero the streak; an untracked ticket class writes nothing; the success path still
counts without a `triage` argument; a `REAL_BLOCK` between two wins no longer prevents
the third win from freezing. Before this file,
`grep -rn record_recipe_outcome harness/tests/` had zero hits — neither call site had
any coverage.

Falsifier — `/tmp/fworker-wq4254/probe_triage_gate.py` runs the same scenario pre-fix
(exemption set emptied) and as shipped:

```
pre-fix (exemption set emptied)    triage=REAL_BLOCK    frozen before=True after=False
as shipped                         triage=REAL_BLOCK    frozen before=True after=True
as shipped, competence signal      triage=GENUINE_BUG   frozen before=True after=False
```

The third line is what keeps the suite honest: a "fix" that merely stopped recording
failures would pass the first two and fail it.

Regression: `dispatcher/tests/test_recipe_crystallizer.py`,
`harness/tests/test_local_rung_escalation.py`, `test_failure_record.py`,
`test_charging.py` + the new file — `111 passed`.

## Risk / rollback

Low. The change is additive — one new frozenset, one early return behind a keyword
argument that defaults to `None`, and one call site passing a value it already had.
Every pre-existing behaviour is reachable unchanged: `triage=None` (the success path)
and every non-exempt class record exactly as before.

The error this introduces is a bias toward keeping a recipe pinned. Bounded, because the
exempt classes all describe tickets where the recipe never got to be wrong: a
`REAL_BLOCK` ticket stopped before producing a diff, a `CAPABILITY_DENIED` one hit a
wall any model hits, and a `HARNESS_DEFECT` one never ran in a valid worktree. Every
class reflecting work the model actually did and got wrong still unfreezes on the first
failure, with no grace window.

Rollback: revert the commit. The recipe store needs no migration — its schema is
untouched, and a stale frozen entry self-corrects on the next competence-bearing
failure.
