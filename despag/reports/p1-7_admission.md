# wq#1812 — despag P1-7: account-global Max admission gate

PHASE0_WIRING_SPEC.md RECOMMENDATION step 7 + A.4 rules 3-5. Cost-protection
ticket: despag runs `claude -p` turns on the SAME Max subscription pool as
Frank's own interactive chat, and it has already drained his session once. No
claude-max turn may reach `REGISTRY.start` without passing this gate.

## What landed

- `despag/admission.py` — the gate. Three checks, cheapest/most-authoritative
  first:
  1. **LIMIT wall** (`_paused_resets_at`, module-global, account-wide): any
     claude-max turn's canonical frames showing a rejected `rate_limit_event`
     (`LimitObserver`, wrapping `classifier.RunState` — the exact detector
     `foreman/harness/fworker.py:1944`'s `saw_limit`/`latest_resets_at` fields
     come from, `foreman/harness/classifier.py:113-210`) pauses admission for
     EVERY session, not just the one that hit it, until `resets_at`.
  2. **`governor_core.evaluate_dispatch`, in-process** — `despag/admission.py:203-218`
     assembles the exact inputs `foreman_usaged.cmd_can_dispatch`
     (`foreman/governor/foreman_usaged.py:364-378`) does (floors, limits, stale,
     stop, live_blocks) via `foreman_usaged.load_floors`/`load_state`/
     `_daemon_stale`, imported and called directly — never a subprocess per turn.
  3. **`burn_rate.allowed_concurrent`** (`despag/admission.py:220-226`) — the
     FRANK_RESERVE_PCT-aware concurrency ceiling (`foreman/dispatcher/
     burn_rate.py:54-58`: session 30% / weekly 15% / fable 10%,
     env-overridable), clamped to `DESPAG_MAX_CLAUDE_CONCURRENT` as an upper
     bound only, counted against turns actually in flight in this process's
     `TurnRegistry` (`despag/runner/turn.py:289-295 TurnRegistry.count_in_flight`),
     never a config constant.
  - litellm-mechanical is exempt from all three (`admission.is_local_model`),
    mirroring `governor_core.evaluate_dispatch`'s own local-rung exemption — it
    costs no Anthropic quota.
  - Fails CLOSED: any exception reading governor state (missing file, corrupt
    JSON, `governor_core`/`foreman_usaged` raising) is treated as
    unreadable/stale, never swallowed into an allow (`despag/admission.py:203-224`).
  - Pre-admission usage read is cached (`DESPAG_USAGE_POLL_SECONDS`, default 60)
    so N concurrent sessions don't hammer the governor's state.json
    (`despag/admission.py:113-135 _read_governor_state`).
- `despag/app.py` — wired into `create_response` (`despag/app.py:579-608`):
  the gate runs before ANY other admission state is touched (idempotency key,
  session lease), so a refusal needs no cleanup. Refusals: `429
  lumen_max_window_exhausted` with a `Retry-After` header when the refusal
  carries a `resets_at` (the LIMIT wall); `429 quota_exhausted` (UHP
  errors.md's own protocol-defined code) otherwise. `GET /v1/uhp` gained a
  `lumen.budget` extension field (`despag/app.py:384-390`,
  `admission.budget_snapshot`) so an operator can see session/weekly percent
  used, the reserve in effect, and `paused_until` without reading state.json
  by hand.
- `despag/runner/turn.py` — `Turn.model` and `TurnRegistry.count_in_flight`
  (`despag/runner/turn.py:64-74`, `289-295`) so the concurrency ceiling counts
  real in-flight turns off the registry, not a separately tracked counter.
- `despag/tests/test_admission.py` — reserve honoured (remaining below reserve
  refuses a running turn even though the governor's own floor alone would
  allow it), LIMIT frame on one session pausing a second session's admission
  with `Retry-After` matching the `resets_at` delta, stale/unreadable governor
  state refusing, governor-internals-raising failing closed, litellm never
  gated (by stale state OR the LIMIT wall), and the `lumen.budget` snapshot
  shape. 11 tests, all passing alongside the pre-existing 121 (132 total,
  `python3 -m pytest despag/tests/ -q`).
- `despag/tests/test_uhp.py`'s `runner_client` fixture now stubs
  `admission.check_admission` open — it exercises session-lease/idempotency
  plumbing against a fake `claude -p` stand-in, not the real Max pool, and
  must stay hermetic against whatever this box's live governor state.json
  happens to say at test time (the gate's own behavior is covered by
  `test_admission.py`, which mocks limits directly).

Not touched: `burn_rate.py`, any governor file, dispatcher config — all three
imported, none edited, per the ticket's explicit constraint.

## Live read-only check

`foreman_usaged.py can-dispatch` (subprocess, for comparison only — despag
itself never shells out per turn):

```
$ python3 /home/lumen/foreman/governor/foreman_usaged.py can-dispatch claude-sonnet-5
{"allow": true, "local_only": false, "reasons": []}
```

The gateway's own `GET /v1/uhp` `lumen.budget` block, read from the identical
governor state.json in-process (`despag.admission.budget_snapshot`), same
poll:

```
$ curl -s http://127.0.0.1:8931/v1/uhp | jq .lumen
{
  "budget": {
    "session_pct_used": 44,
    "weekly_pct_used": 50,
    "reserve_pct": {
      "session": 30.0,
      "weekly_all": 15.0,
      "fable_weekly_scoped": 10.0
    },
    "resets_at": "2026-09-12T17:20:00.846098+00:00",
    "paused_until": null
  }
}
```

Both reads agree: `session_pct_used=44` and `weekly_pct_used=50` are each well
above their governor floors (currently zeroed fleet-wide per Frank's
2026-08-23 instruction, `foreman/governor/floors.json`) and, before the
reserve, would allow — matching `can-dispatch`'s `allow: true`. `paused_until:
null` confirms no LIMIT wall is active, consistent with `local_only: false`.
The reserve values shown (`reserve_pct`) are the ones `allowed_concurrent`
actually used to compute the concurrency ceiling in the same request.
