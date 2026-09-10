# wq#1737 — governor stale fail-closed vs zeroed floors

## Root cause (evidence grade A: live state.json + daemon journal, 2026-09-10)

- `/home/lumen/foreman/state.json`: last_good_at 08:50:30Z, primary_fail_streak 73,
  cache_age_s 42511, stale=true. Every daemon poll since 08:50Z: `primary fetch failed:
  HTTP 429` on /api/oauth/usage, backoff pinned at 600s. CCC fallback unprovisioned.
- `governor_core.evaluate_dispatch` line 197: `if stale or not limits: deny`. That branch
  runs before any floor is consulted, so it denies even when no floor could ever deny.
- `governor/floors.json`: session/weekly/fable floors all 0, soft_margin 0 (Frank
  2026-08-23, 3rd instruction: only cloud_ceiling limits). With floors at 0 a FRESH
  reading can only deny when a bucket is missing (remaining None). The stale branch is
  therefore the only thing the usage reading does, and all it does is convert an
  endpoint 429 streak into a fleet-wide dispatch outage (12h, every pass governor_deny).
- Nothing paged: `stale` edge sends one ntfy at the flip (08:50Z + 600s) and nothing
  after. No aq row. aq#1195 was opened by hand at 20:43Z by the finder.

## Class

A telemetry read that no longer gates anything is still able to stop the fleet.
Same class: `router.governor_pressure` returns `deny` on `percent is None` regardless of
the floor on that pool (orchestrator/router.py:743). wq#1458 (dead-daemon computed
staleness) and wq#1669/#1699 (tight-mode vs zeroed floors) are adjacent but do not cover
the stale->deny branch; #1458 stays fail-closed by design and this change keeps that
posture whenever a floor is non-zero.

Real quota exhaustion is caught in-band: harness fworker reports rejected
rate_limit_event frames via `report-event` -> events.json -> live_blocks, which
evaluate_dispatch checks BEFORE staleness. STOP is checked first of all. Those two are
the hard gates and remain hard.

## Options

1. Drop the stale branch entirely. Rejected: non-zero floors need fail-closed (module
   docstring, README, wq#1458 all promise it).
2. Make stale advisory only when every floor that applies to the model is 0. Chosen:
   zero floors mean the reading has no authority to deny; live_blocks + STOP still deny.
3. Provision the CCC cookie so the primary 429 does not matter. Orthogonal, needs a
   credential Frank mints; does not fix the class (a second endpoint can also 429).

## Change

governor_core:
- `applicable_floors(model, floors, now_utc)` -> dict of the floors evaluate_dispatch
  would consult for this model (session, weekly_all, + fable for fable models).
- `floors_zeroed(model, floors, now_utc)` -> True when every applicable floor == 0.
- evaluate_dispatch: order unchanged (STOP -> local exempt -> live_blocks). Then:
  - stale/missing limits AND floors_zeroed -> allow, reason
    `stale usage, floors zeroed: advisory only`.
  - stale/missing limits AND any non-zero floor -> deny, unchanged wording.
  - fresh path: a bucket with `remaining is None` only denies when that pool's floor
    is > 0 (same class, same rule).

foreman_usaged.poll_once: on the stale edge (cache_age_s > stale_fail_closed_seconds)
`aq add foreman-governor usage-stale ...` (dedups onto the open row, bumps count);
on recovery resolve the open row. ntfy on the edge stays, but the title only says
FAIL-CLOSED when a floor is non-zero; with floors zeroed it says advisory. aq calls go
through `/home/lumen/bin/aq` as a subprocess (same shape as ntfy-send), AQ_DB
inherited so tests can point it at a scratch db; failures are logged, never raised.

router.governor_pressure: `percent is None` on a zero-floor pool is skipped instead of
`deny`.

## Test to failure

governor/test_governor_core.py (4 cases from the ticket + 2 class cases):
- zero floors + stale -> allow, reason names advisory
- non-zero floors + stale -> deny (unchanged)
- live_block + zero floors + stale -> deny
- STOP + zero floors -> deny
- fable model with zero session/weekly but non-zero fable floor + stale -> deny
- fresh limits, bucket missing, zero floor -> allow
governor/test_foreman_usaged.py: stale edge calls aq add once, recovery resolves.
orchestrator/tests/test_router.py: missing bucket on zero-floor pool -> not deny.
Replay: `foreman_usaged.py can-dispatch claude-sonnet-5` against the live state.json
(stale=true, cache_age 42k) must allow; then the next lumen-foreman-dispatch pass must
spawn instead of governor_deny.

## Rollback

`git revert` the squash commit; can-dispatch is a fresh subprocess per call so the
revert is live on the next pass. Restart the usaged daemon after reverting so the aq
edge code is unloaded too.

## Not fixed here

- Why /api/oauth/usage 429s for 12h at a 600s cadence: research prompt queued in
  scratch/research_prompts/ (ticket item 3).
- Daemon persistence (transient lumen-run unit): wq#1458.
