# wq#1859 — Adversarial verify-pass of wq#1857 (despag P1-2: re-integrate wq#1807 Idempotency-Key admission + session-lease fencing into the runner)

**Verdict: CONTRADICT** — the two defects wq#1853 filed against wq#1806 (no admission
path at all, `timeout_seconds=0` falsy bug) are both genuinely fixed, and the fix is
proven by tests that would fail without it (verified live, not just read). But the
session-lease half of the fix has a gap the shipped tests never exercise: the lease is
admitted once at turn start with a 300s TTL (`SESSION_LEASE_TTL_S`) and **never
renewed** for the life of the turn. `despag/backing/control_sqlite.py`'s `lease_renew`
exists and is unit-tested in isolation (`test_control.py`) but is not called anywhere in
`despag/app.py` or `despag/runner/`. Any turn that runs longer than 300 wall-clock
seconds — routine for this system, whose own frame-gap watchdog defaults to tolerating
900s of silence between frames and whose wall-clock cap defaults to 21600s — silently
loses its session lease mid-flight. A second request against the same session
(`previous_response_id` pointing at the still-running turn's response) is then admitted
as a **second concurrent `claude -p` in the same session workspace**, which is exactly
the double-spawn failure mode this ticket exists to close. Reproduced live below, not
inferred.

## What was shipped (confirmed correct, as far as it goes)

`despag/app.py:create_response` now runs both wq#1807 admission gates before
`REGISTRY.start` (`app.py:494-534`): `_idem_admit` (`app.py:417-435`) reserves an
`Idempotency-Key`, replaying a settled winner's response or answering `409
lumen_still_running` for one still in flight; `CONTROL.lease_admit(sid, response_id,
reject_on_conflict=True)` (`app.py:526`) admits the *resolved* hsess id and answers
`409 session_busy` on an active conflict. `_settle_turn` (`app.py:438-455`) is the only
place either is released, so a background turn (which outlives the request) keeps both
until it actually finishes. `despag/runner/turn.py:76` fixes the falsy-zero bug: `(...
if timeout_seconds is None else max(0.0, min(timeout_seconds, max_turn_cap)))`
correctly treats `timeout_seconds=0` as the smallest legal cap, not "unset."

**Falsified both gates myself to confirm they are load-bearing, not just present:**
- Forced `idem_key = None` in `create_response` → `test_idempotency_key_replays_the_
  winner_instead_of_a_second_turn` and `test_idempotency_key_still_running_is_409` both
  fail (2 failed, 21 passed). Reverted (`git checkout -- despag/app.py`), suite green
  again (94 passed).
- Forced `CONTROL.lease_admit(..., reject_on_conflict=False)` → `test_second_turn_of_a_
  running_session_is_409_session_busy` fails (1 failed, 22 passed). Reverted, suite
  green again (94 passed).
- `python3 -m pytest despag/tests/ -q`: **94 passed**, live in this worktree, matching
  `evidence.json`'s cited command/exit_code.

## Finding — session lease has no renewal path; a turn over 300s loses its fence

`SESSION_LEASE_TTL_S = 300` (`app.py:93`) is the only `ttl_s` ever passed to
`lease_admit` (`app.py:526`), and `lease_admit`'s own conflict check
(`control_sqlite.py:176`, `live = expires > now`) treats an expired lease as absent —
by design, so a crashed turn's lease doesn't wedge the session forever
(`control_sqlite.py:159-161` docstring). That design is correct for a crash. It is not
safe for a *live, still-running* turn, because nothing calls
`ControlStore.lease_renew` — the heartbeat method that exists for exactly this — from
anywhere in the request path or from `Turn`/`TurnRegistry`. `grep -rn "lease_renew"
despag/` finds it only in its own definition and its own isolated unit test
(`test_control.py:167-172`); `_settle_turn` only ever *releases* the lease at the end.

**Reproduced live** (throwaway repro script, run then deleted — not left in the repo):
shrank `SESSION_LEASE_TTL_S` to 0.5s (the same mechanism a 6-hour turn hits at 300s,
just compressed to make it reproducible in-session) and pointed `claude_max.build_turn`
at `fake_backend.py --mode steady` so the "turn" keeps running past the shrunk TTL:

```
r1 200 in_progress                       # background turn starts, holds the lease
turn still running: True                 # ...and is still running 1s later
r2 200 {'status': 'completed', ...}      # a SECOND request for the SAME session
                                          # (previous_response_id=r1's id) was ADMITTED
                                          # and ran a full second turn to completion
                                          # while the first was still in flight
```

The expected result — matching `test_second_turn_of_a_running_session_is_409_session_
busy`, which only exercises a turn shorter than the TTL — is `409 session_busy` for r2.
Instead r2 runs an independent `claude -p` (fake_backend stand-in) concurrently with r1
in the same session/workspace, which is precisely "two `claude -p` children in flight"
for one session — the exact guarantee this ticket's own module docstring
(`app.py:14-16`) claims: *"the resolved session ... is admitted through a per-session
lease that fails closed with 409 session_busy, so one session never has two `claude -p`
children in flight."* That claim is false for any turn exceeding `SESSION_LEASE_TTL_S`
(300s), which is a routine, not exceptional, duration for this system — the frame-gap
watchdog alone defaults to tolerating 900s of silence between frames
(`turn.py:35 DESPAG_FRAME_GAP_SECONDS_DEFAULT = 900`), and the wall-clock cap defaults
to 6 hours (`turn.py:31`).

Not covered by any shipped test: every admission test in `test_uhp.py` that exercises
`session_busy` uses `fake_backend.py --mode steady --count 400 --interval 0.05` (a
~20s-long fake turn), well under the 300s TTL, so the gap between "lease admitted" and
"lease actually still live when it matters" is never stressed.

## Bottom line

CONTRADICT. wq#1853's two original findings against wq#1806 are correctly fixed and
proven by tests that fail without the fix (verified by deliberately breaking each gate
and watching the right tests fail, then confirming green again). But the re-integration
introduces — or rather, fails to close — a third gap: `lease_admit` without a paired
`lease_renew` heartbeat only protects a session for `SESSION_LEASE_TTL_S` (300s), not
for the life of the turn, so the "one in-flight turn per session" guarantee the ticket
and its own docstring claim silently fails for any turn longer than 5 minutes. Filed as
a follow-up fix ticket per this archetype's escalation rule (contradiction → judgment
trigger, `claude-opus-5@high`), deps wq#1857: heartbeat the lease (e.g. a periodic
`lease_renew` call from `_settle_turn`'s wait loop, or from `Turn.run`'s own frame-loop
tick) so its liveness tracks the turn's actual wall-clock progress instead of a fixed
TTL shorter than the turn's own advertised tolerances.
