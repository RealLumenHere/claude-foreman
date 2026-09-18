# wq#1996 — verify-pass: wq#1994's denv fixture spawn_reserve.db isolation

**Verdict: CONFIRM.** Adversarial re-check of the diff shipped by wq#1994
(claude-foreman http://100.121.17.5:3000/lumen/claude-foreman/pulls/622,
commit `d67d542`). The fix does what it claims: it stops
`dispatcher/tests/test_dispatcher.py` from reading/writing the real
production `dispatcher/runtime/spawn_reserve.db`, and gives every test its
own isolated reservation store.

## The diff

`dispatcher/tests/test_dispatcher.py` +5, one file:

```
monkeypatch.setattr(spawner, "SPAWN_RESERVE_DB", str(tmp_path / "spawn_reserve.db"))
```

added to the `denv` fixture, mirroring `test_spawner.py`'s existing
`spawner_env` fixture pattern (wq#1815).

## 1. Mechanism — CONFIRMED sound by code inspection

`harness/spawner.py:92-94` defines `SPAWN_RESERVE_DB` as a module-level
global (`os.environ.get("FOREMAN_SPAWN_RESERVE_DB", .../dispatcher/runtime/spawn_reserve.db)`).
`reserve_spawn`, `release_spawn`, and `settle_spawn` (lines 154, 175, 189)
each do `store = ControlStore(SPAWN_RESERVE_DB)` — a fresh module-attribute
lookup on every call, not a value cached at import time. `test_dispatcher.py`
does `import spawner` (plain module import, not `from spawner import X`), so
`monkeypatch.setattr(spawner, "SPAWN_RESERVE_DB", ...)` patches the exact
object every call site reads, including indirect calls through
`foreman_dispatcher.py`'s `spawner.dispatch_one(...)` (dispatcher/foreman_dispatcher.py:1510),
which references `spawner.` by attribute access too. No cached/bound copy
anywhere in the call chain that this monkeypatch would miss.

## 2. Full suite — CONFIRMED green, fresh run

```
python3 -m pytest dispatcher/tests/test_dispatcher.py -q
122 passed in 265.31s (0:04:25)
```

Run cold in this worktree (HEAD `b7b99a8`, wq#1994's commit `d67d542` two
commits back). Zero failures, zero the file's own historical
spawn-reservation-collision signature ("spawn reservation already held —
duplicate spawn, refusing before Popen (E.5)").

## 3. No production leak — CONFIRMED by filesystem check

Before the run, `dispatcher/runtime/` did not exist in this worktree. After
the full 122-test run it *still* does not exist — `ls dispatcher/runtime/`
→ `No such file or directory`. Instead each test wrote its own
`spawn_reserve.db` under pytest's own tmp_path, e.g.:

```
./.tmp/pytest-of-lumen/pytest-0/test_repeated_spawns_with_no_f0/spawn_reserve.db
./.tmp/pytest-of-lumen/pytest-0/test_a_held_ticket_is_not_re_f0/spawn_reserve.db
./.tmp/pytest-of-lumen/pytest-0/test_burn_rate_cap_shrinks_whe0/spawn_reserve.db
... (one per test)
```

One file per test function, confirming both halves of wq#1994's claim:
tests no longer touch the real production reservation store, and two tests
reserving `(ticket=1, attempt=1)` can no longer collide across tests since
each test's store starts empty.

## Scope note — a related, already-tracked, out-of-scope issue

ISSUE-LOG.md row #415 (filed same day by whoever hit wq#1721) already
documents that wq#1994's isolation is "necessary but not sufficient" for a
*different* failure mode: a single test that calls `run_pass()` repeatedly
for the *same* `(ticket=1, attempt=1)` can still self-collide within that
one test, because denv's faked `lumen-run` path never calls
`spawner.settle_spawn()` the way real `fworker.py` does. That is a distinct
defect in a different subsystem (owned by wq#1893/wq#1703's spawn-loop
guard), already recorded with a follow-up ticket (status PATH) — not a
false claim in wq#1994's diff, which never claimed to fix within-test
reuse. The full suite passing 122/122 here confirms that scenario is not
currently manifesting (a later commit, spawn-loop-guard 9fb0f9c, appears to
have addressed it independently) — so it does not contradict this specific
verify target either.

## Conclusion

wq#1994's diff is minimal (1 file, +5), scoped exactly to its stated
problem, mechanically sound (module-global re-read at call time, matching
the proven `test_spawner.py` pattern), and verified live: full suite green,
no production file touched, isolated per-test stores confirmed on disk.
**CONFIRM.**
