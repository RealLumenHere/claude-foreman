# wq#2008 — verify-pass: wq#2004 dispatcher tests red on main (PR#628)

**Verdict: CONFIRM** — PR#628 is a test-only diff (`dispatcher/tests/test_dispatcher.py`,
+41/-8, one file) that fixes the 10 originally-failing tests without weakening any of
their discriminators. `dispatcher/tests/test_dispatcher.py` runs 122/122 passed
standalone; the full `dispatcher/tests/` suite runs 352/352 passed. No production code
changed.

PR#628, merged to main as `dfbcaef` (`git merge-base --is-ancestor dfbcaef origin/main`
confirms ancestry).

## What the diff does

Three independent edits, addressing two distinct root causes named in wq#2004:

1. **`loop_guard` fixture** (`test_dispatcher.py:1589`): adds
   `monkeypatch.setattr(spawner, "DEAD_OWNER_GRACE_S", 0)` alongside the existing
   `SPAWN_LOOP_MAX = 2`. This is the fix for the wq#1893-vs-wq#1703 collision wq#2004
   diagnosed: `reserve_spawn()` (`spawner.py:233`) runs before the spawn-loop guard
   (`foreman_dispatcher.py:1333`) ever sees a ticket, and with the default 60s grace a
   dangling reservation from a simulated crash is "too young to judge" on the very next
   test-time pass, so `dispatch_one` returns `None` before the guard gets a second real
   spawn to count. Zeroing the grace lets `reservation_owner_alive()` (which `crash_pass`
   falsifies by clearing the fake unit list) become the only signal, matching the idiom
   wq#1893's own suite already uses for the same reason
   (`harness/tests/test_spawner.py:189`, `:208`).
2. **`test_burn_rate_cap_replaces_binary_tight_clamp`**: input changed from 70% to 55%
   session-used. Unrelated to the loop-guard bug — caused by `c7cd075` (merged before
   wq#2004, `burn_rate: reserve interactive headroom for Frank`) adding
   `FRANK_RESERVE_PCT`, which the test (written at `fee4c54`, before the reserve existed)
   never accounted for.
3. **`test_sec5_zero_floors_critical_engages_scaled_ceiling`**: expected ceiling changed
   from `cloud_ceiling` (3) to `1`, plus new assertions on the `burn_rate` reason string
   and a `halt`/`concurrency`/`no_slots` event. Same root cause as #2.

## Verification performed (adversarial, cold read + math replay + live run)

1. Read `git show dfbcaef` cold, not the PR body's own framing.
2. **Replayed the burn-rate math by hand** against `dispatcher/burn_rate.py`'s actual
   `pool_spend_rate`/`allowed_concurrent` (not the docstring's claim):
   - Test 1 (55% session, 30min reset; 10% weekly, 48h reset; no `orch-*` rows in the
     test's `wq` db, so `frank_active()` is `False` and the non-bumped reserve applies):
     session `(100-55-30)/0.5 = 30%/h / 1.5 = 20`; weekly `(100-10-15)/48 = 1.5625%/h /
     0.35 = 4.46`; `min(20, 4.46)` clamped to `[1, cloud_ceiling=3]` = **3**. Matches the
     docstring and the unchanged assertion `[t1, t2]` both spawn (ceiling 3 ≥ 2 tickets).
   - Test 2 (80% session/40min; 83% weekly/4h): session `(100-80-30)` floors at 0 ->
     `n=0`; weekly `(100-83-15)/4 = 0.5%/h / 0.35 = 1.43`; `min(0, 1.43)` clamped to
     `[1, 3]` = **1**. Matches the new assertions (`ids[:1]`, `"burn-rate ceiling 1"`).
   Both computations match the diff's own comments exactly — no arithmetic error, no
   assertion quietly loosened to fit an arbitrary observed output.
3. **Checked the diff's line-number citations against the actual files** rather than
   trusting them: `foreman_dispatcher.py:1333` is exactly the
   `spawn_loop_guard.looping(state, tid)` call; `spawner.py:233` is exactly
   `if not reserve_spawn(ticket_id, attempt, owner):`; `test_spawner.py:189`/`:208` are
   exactly the two `DEAD_OWNER_GRACE_S = 0` monkeypatches the diff claims mirror. All
   four citations are accurate.
4. **Adversarial question: does zeroing `DEAD_OWNER_GRACE_S` in the dispatcher's
   `loop_guard` fixture blind the suite to the grace period it's supposed to protect?**
   No — `test_spawner.py:147`'s
   `test_double_spawn_reservation_blocks_racing_duplicate` runs with the real default
   grace (it never monkeypatches it) and exercises exactly the race the grace period
   exists for: two concurrent `dispatch_one()` calls on a *fresh* reservation, where one
   must lose to the "already held, refuse" path rather than a dead-owner reclaim. The
   grace-period behavior stays covered; only the dispatcher's own loop-guard tests
   (which need repeated same-attempt spawns across passes fired microseconds apart, not
   minutes) bypass it, and only via the same monkeypatch pattern wq#1893's suite
   established for the same reason.
5. **Ran the shipped fix, not just read it.** `pytest dispatcher/tests/test_dispatcher.py`
   (the file wq#2004 named as reproducing 10 failed/112 passed): **122 passed, 0
   failed**. `pytest dispatcher/tests/` (the acceptance criterion's literal command):
   **352 passed, 0 failed** — full green, matching wq#2004's stated acceptance
   ("pytest dispatcher/tests/ green") exactly, not just partially (the ticket's own notes
   record an intermediate 350p/2f state before the burn-rate test edits landed in the
   same commit).

## Why this holds up

- The fix is test-only: it cannot have broken any production path, and the diff doesn't
  touch `spawner.py`, `foreman_dispatcher.py`, or `burn_rate.py`.
- Neither of the two root causes is papered over. The loop-guard fix names a real
  precedent (wq#1893's own suite) for the exact idiom used, rather than inventing a new
  one. The burn-rate fix is a straightforward test-data update after a legitimate,
  already-merged production semantics change (`c7cd075`), not a rationalization of wrong
  output.
- The discriminating assertions in both burn-rate tests (that the controller — not
  `tight_mode`'s binary clamp — owns the ceiling, and that p2 is never `tight_pri`-
  filtered) are preserved unchanged; only the numeric inputs/expected ceiling moved to
  match the reserve that now sits in front of them.

## Bottom line

CONFIRM. No escalation; no ISSUE-LOG addition (no new defect found — wq#2004's own
ISSUE-LOG #415 entry already covers the root cause this diff fixes).
