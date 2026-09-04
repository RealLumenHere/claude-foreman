# wq#1419 — Adversarial verify-pass of wq#1408 (dispatcher per-ticket exception boundary)

**Verdict: CONTRADICT** — the per-ticket exception boundary itself (§3.1's core ask: one
bad ticket must never abort the whole pass) is correctly implemented and its 10 new
tests (one per §1.5 audit-table row) pass for the right reasons. But the same commit
also moved `circuit_breaker.check_and_trip()` — the dead-man's-switch stage 0.5 halt —
from running **before** `load_config()` to running **after** it succeeds. This reverses
the exact invariant the code's own retained comment claims to preserve ("checked before
config load so a broken config.json can never suppress a trip"): a genuinely broken
committed `config.json` now makes the pass bail out on a `ConfigError` before the
circuit breaker ever runs, so a real failure-rate trip — DISARM write, halt log, ntfy
page — is silently skipped in favor of a generic "invalid config" error. The pre-existing
regression test with a name that claims to guard this
(`test_circuit_breaker_trips_disarms_and_halts_before_config_load`) does not actually
catch it, because it dirties `config.json`'s working-tree copy without committing it —
per wq#772 `load_config()` only ever reads `git show HEAD:config.json`, so that test's
"broken" config is invisible to the daemon and the test passes independent of ordering.

## What was shipped

PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/286 (merged as `954ce5a`,
squash-identical to branch tip `e01cc23`): one try/except quarantine boundary around
each per-ticket loop body in `dispatcher/foreman_dispatcher.py::_do_pass` (skip +
continue on any `Exception`, re-raise `SystemExit`/`KeyboardInterrupt`), a per-repo
`repo_gate_check()` cache so a broken preflight profile costs one exception per repo per
pass instead of one per ticket, `router.wq()` gaining `timeout=30`, the three per-pass
preamble calls (`runaway.stuck_ticket_ids`, `psi_pressure.check`, `pacing.load_config`)
degrading to documented safe defaults on exception, and `circuit_breaker.check_and_trip`
moving from before `load_config()` to inside `run_pass`'s later `try` block. 10 new
tests, one per §1.5 row.

## Mechanics — CONFIRMED

Checked out the PR branch tip (`e01cc23`) and ran the full dispatcher suite:

```
python3 -m pytest dispatcher/tests/ -q
```

All pass (76 in `test_dispatcher.py` alone, including the 10 new wq#1408 tests). Traced
every row of the §1.5 audit table against the diff:

- `dispatch_gate_check`/preflight (`repo_gate_check`, cached per repo per pass,
  `errored=True` routed to the new `handle_profile_unreadable` with its own 1h ntfy
  dedup) — correct, matches `test_profile_unreadable_skips_repo_without_crashing_pass`.
- `router.wq_show`/`router.repo_for_ticket`/`router.assign`/`PACING_HOOK`/`save_state` —
  all now inside the per-ticket `try`, all caught generically by the boundary if not
  caught more specifically first; each has a passing test.
- `router.wq()` timeout=30 on the subprocess call, with `wq_show`'s own `TimeoutExpired`
  given the friendlier "vanished" skip reason rather than a generic quarantine.
- `circuit_breaker.check_and_trip()` moved inside the pass's outer `try` so an exception
  from it is now a logged `error` event instead of a bare traceback — this part of the
  change is correct and has a passing test
  (`test_circuit_breaker_exception_logs_error_not_bare_traceback`).
- Per-ticket `break`s that remain inside the new per-ticket `try` (concurrency-ceiling
  halt, tight-mode spawn-cap halt) are unaffected by the surrounding `except Exception`
  — `break` is loop control, not something an `except` intercepts — so pass-level halts
  still halt correctly.

## Contradiction — circuit breaker can be silently suppressed by a broken config

`run_pass`'s stage ordering, before this PR:

1. `kill_switch()`
2. `circuit_breaker.check_and_trip()` (unguarded — the bug wq#1408 was fixing: an
   exception here was a bare traceback)
3. `load_config()` (fail-closed `try/except ConfigError`)

After this PR, `circuit_breaker.check_and_trip()` was relocated to run *inside* the
`try:` block that starts **after** step 3's `load_config()` has already succeeded and
returned — i.e. the ordering is now `kill_switch → load_config → circuit_breaker`. The
PR's own retained comment at the call site still reads (`dispatcher/foreman_dispatcher.py`
around `run_pass`'s `rc = 0` block):

> "stage 0.5 (wq#718): dead-man's switch. wq#1408: moved inside this try — it used to run
> before config load with no boundary of its own... Now it's caught by the same pass
> boundary as everything else."

The comment documents *that* it moved, but not that moving it past `load_config()`
reverses the pre-existing invariant a sibling stage-0.5 comment states verbatim
elsewhere in the same function: "checked before config load so a broken config.json can
never suppress a trip."

### Reproduction

Using the project's own test fixtures (`denv`, `write_attempt`, `commit_config` from
`dispatcher/tests/test_dispatcher.py`), committed (not just working-tree-dirtied) a
genuinely invalid `config.json`, with 5 KILLED attempts already on record (comfortably
over the breaker's default threshold):

```python
monkeypatch.setattr(circuit_breaker, "WINDOW_MINUTES", 90)
monkeypatch.setattr(circuit_breaker, "MIN_SAMPLES", 5)
monkeypatch.setattr(circuit_breaker, "FAILURE_RATE_THRESHOLD", 0.75)
for i in range(5):
    write_attempt(denv, 900 + i, 1, "KILLED")
add_ticket("eligible but a broken committed config arrives first")
denv["cfg_path"].write_text("{not json")
commit_config(denv["tmp"], "break config for real")   # unlike the existing test, this
                                                        # actually lands on HEAD
rc = fd.run_pass()
```

Output:

```
{"event": "error", "stage": "config", "reason": "config .../config.json: invalid JSON
 in committed config: Expecting property name enclosed in double quotes: ..."}
rc= 1
halts= []
DISARM exists= False
ntfy= ['... dispatcher: invalid config — pass aborted: ...']
```

No `circuit_breaker` halt event, no `DISARM` file, no breaker-trip ntfy — only the
generic config-error page. The failure-rate trip that should have fired (5/5 KILLED,
threshold 0.75) never runs at all.

### Why the existing regression test doesn't catch it

`test_circuit_breaker_trips_disarms_and_halts_before_config_load` (pre-existing, name
unchanged by this PR) writes `denv["cfg_path"].write_text("{not json")` directly and
never calls `commit_config`/`write_config`. Per wq#772 (`_git_show_committed`),
`load_config()` only ever reads `git show HEAD:config.json` — an uncommitted
working-tree edit is invisible to it by design ("an uncommitted edit... must not change
live policy"). So in that test `load_config()` keeps reading the last good committed
`BASE_CONFIG`, never raises, and the breaker check runs and passes regardless of which
side of `load_config()` it sits on. The test's own doctrine (`write_uncommitted_config`
exists specifically for "must never become live policy" cases) makes this look
deliberate, but for *this* test the effect is that it provides no coverage of real
config-parse failures at all, and so didn't catch the reordering.

### Impact

Circuit-breaker trips are exactly for failure-spiral conditions; a config edit landing
broken at the same time (correlated infra trouble, a bad manual edit, a botched
autonomous config change) is a plausible coincidence, not a contrived one. Under this
ordering, a fleet failing badly enough to warrant the dead-man's-switch — DISARM +
urgent ntfy — instead gets only a "pass aborted: invalid config" page, silently losing
the specific failure-rate diagnostic (`breaker_stats`) an operator would want when
deciding whether to re-arm.

## Disposition

Fix is narrow: move `circuit_breaker.check_and_trip()` back to run before
`load_config()`, keeping it inside its own small `try/except Exception` (the actual
wq#1408 fix for the "bare traceback" bug) rather than folding it into the later
config-dependent `try` block. Not re-implementing here per verify-pass scope — filing
this contradiction for the fix to land as its own change.

## Secondary note (not blocking the verdict)

`save_state(state)` inside the per-ticket loop runs *after* `spawner.dispatch_one()` has
already returned a real unit and *before* `log.emit("spawn", ...)`. If `save_state`
raises there (§1.5 row :912, exercised by
`test_save_state_exception_quarantines_ticket_without_crashing_pass`), the boundary logs
`skip ... reason=quarantined` for a ticket that was, in fact, actually dispatched — no
`spawn` event is ever logged, and the `state["lane_counts"]` increment for that spawn is
lost on disk (the next pass calls `load_state()` fresh from disk). This doesn't cause a
double-dispatch (stage 7's `already_running` check reads live `systemd` units, not the
log/state), but it does mean the daily lane cap can silently undercount a real spawn,
and dispatcher.log misreports an actually-running ticket as skipped. The shipped test
documents this behavior rather than treating it as a defect; flagging it here since it's
a real, if narrow-window, telemetry/bookkeeping gap the ticket's own audit table didn't
anticipate.
