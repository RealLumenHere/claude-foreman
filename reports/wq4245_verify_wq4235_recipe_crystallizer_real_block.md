# wq#4245 — verify-pass of wq#4235 (evolving-agents crystallize/freeze recipe pattern)

**Verdict: CONTRADICT.** Escalating to opus-5@high per ticket instructions (filed
wq#4254, deps wq#4235, archetype `judgment`).

Verifier: claude-sonnet-5@high, fresh instance (same model as author). Read the
PR cold (`bc125f28`, landed main@`dc08863d0f` / lumen/claude-foreman#2163), did
not re-implement.

## What wq#4235 shipped

`dispatcher/recipe_crystallizer.py` (new, 197 lines) tracks a per-
`(ticket_type, model, prompt_template)` cell's consecutive-success streak;
after `FREEZE_THRESHOLD` (3) wins in a row it freezes a recipe for that
ticket_type, and `dispatcher/foreman_dispatcher.py:candidate_model()` routes
matching tickets straight to the frozen model instead of calling
`router.assign()`. `harness/fworker.py:record_recipe_outcome()` feeds outcomes
back in from two call sites: `reconcile()` on confirmed SUCCESS/DEGRADED_SUCCESS
(`success=True`, fworker.py:4377) and `handle_failure()` on terminal block
(`success=False`, fworker.py:4715). 9 new tests
(`dispatcher/tests/test_recipe_crystallizer.py`) all pass; I reran them clean.

## The defect

`handle_failure()` (harness/fworker.py:4664-4722) computes a mechanical
`rec["triage"]` for the terminal failure before it does anything else
(`record_terminal_failure()`, line 4684). Two lines later it already
special-cases one triage value explicitly:

```
4696: # REAL_BLOCK (the worker itself decided this needs a human/real decision),
...
4701:     if (is_local_rung(args.model)
4702:             and rec["triage"] != failure_record.TRIAGE_REAL_BLOCK
4703:             and rec["triage"] != failure_record.TRIAGE_CAPABILITY_DENIED
...
4714:    log(f"BLOCKED [{block_class}/{rec['triage']}]: {reason}")
4715:    record_recipe_outcome(ticket, args, success=False)
4716:    if rec["triage"] != failure_record.TRIAGE_REAL_BLOCK:
4717:        wq_block(ticket_id, reason)
```

Line 4716 (one line below the bug) correctly skips re-blocking when the triage
is `REAL_BLOCK` — that's the comment at 4675-4677's "good path": the worker
itself already legitimately called `wq block` (e.g. per this same fleet's
standing protocol: "If a decision the ticket marks as Frank's is not already
recorded in the ticket, `wq block <id> "<reason>"` and stop"). But line 4715,
immediately above, calls `record_recipe_outcome(ticket, args, success=False)`
**unconditionally** — no reference to `rec["triage"]` at all. The same gap
applies to `TRIAGE_CAPABILITY_DENIED` (a sandbox policy denial no model can
get past) and `TRIAGE_HARNESS_DEFECT` (a worktree/repo mismatch — a harness
bug, not a model-competence signal).

None of these three triage classes says anything about whether the
`(model, prompt_template)` combo is bad at solving this ticket class. A
`REAL_BLOCK` in particular means the model did the *right* thing — correctly
recognized a Frank-only decision and stopped — yet it is recorded as a
recipe loss identically to an actual bad diff or crash (`TRIAGE_GENUINE_BUG`).
This directly undermines wq#4235's own stated purpose (`recipe_crystallizer.py`
docstring: avoid "noise dressed up as a pattern, not a recipe") on exactly the
routine path the fleet's own worker protocol produces constantly.

## Reproduction (live, this worktree)

`/tmp/fworker-wq4245/repro_real_block_unfreeze.py`, importing the real
`recipe_crystallizer` and `fworker` modules (not mocked), against a scratch
store (`/tmp/fworker-wq4245/recipes_repro.json`):

```
$ python3 repro_real_block_unfreeze.py
frozen before REAL_BLOCK event: {'frozen_at': ..., 'model': 'claude-sonnet-5',
  'prompt_template': 'test-fix', 'streak_at_freeze': 3}
frozen after REAL_BLOCK event: None
```

Three `record_recipe_outcome(ticket, args, success=True)` calls (matching
`reconcile()`'s wiring) freeze a `test-fix` recipe on `claude-sonnet-5`. One
more call with `success=False` — the exact call shape `handle_failure()` makes
at line 4715 for *any* terminal block, REAL_BLOCK included — unfreezes it.
There is nothing in the real code path that would stop this from happening on
an actual REAL_BLOCK ticket: `wq_block()` still runs at 4717 for every other
triage, so REAL_BLOCK is the one case already carved out for *something* in
this function, just not for the recipe write one line up.

## Coverage

- `dispatcher/tests/test_recipe_crystallizer.py` (9/9 pass, reran clean): covers
  `classify_ticket_type`/`record_outcome`/freeze/unfreeze purely inside
  `recipe_crystallizer.py`, with hand-fed `True`/`False`. None of them touch
  `harness/fworker.py`'s classification-to-`success` mapping.
- `grep -rn "record_recipe_outcome" harness/tests/` — no hits. No test exists
  for either `fworker.py` call site, so the triage-insensitivity was never
  exercised before landing.
- Read `dispatcher/foreman_dispatcher.py`'s `candidate_model()` wiring
  (lines 668-678): correctly gated behind the same `cli_canary.held()` check
  as the explicit-tuple path above it — no defect found there.
- `recipe_crystallizer.py`'s own freeze/unfreeze/lookup logic (lines 148-197):
  read carefully for races and off-by-ones (streak reset on loss, single-
  recipe-per-ticket-type exclusivity via `not frozen`, cell isolation across
  different `(model, prompt_template)` pairs) — correct. One secondary,
  non-blocking note: the module docstring (lines 23-26) claims "atomic
  tmp+rename on write (foreman_dispatcher.py's `save_state()` pattern)" but
  `_save_locked()` (lines 99-104) actually does an in-place `truncate()`+
  `write()` on the already-`flock`'d fd, not a tmp+rename. This is still safe
  for the writer (lock-held, and `write()` lands at the truncated EOF==0
  regardless of the `"a+"` mode's `O_APPEND` semantics), and `load_store()`'s
  unlocked read already fails open to "nothing frozen" on a transient empty
  read, so this is a doc/implementation mismatch rather than a correctness
  bug — flagging it for the record, not blocking on it.

## Disposition

Did not re-implement. Filed **wq#4254** (lane build, pri 2, deps wq#4235,
model claude-opus-5, effort high, archetype judgment) for the judgment call:
whether to gate the fix (exempt REAL_BLOCK/CAPABILITY_DENIED/HARNESS_DEFECT
from `record_recipe_outcome`'s failure path, e.g. by checking `rec["triage"]`
before the call at fworker.py:4715) as a reopen of wq#4235 or a fast-follow,
given the frozen-recipe routing shortcut itself (`foreman_dispatcher.py`) is
otherwise correct and the blast radius is "recipes thrash more than intended,"
not data loss or a bad dispatch.
