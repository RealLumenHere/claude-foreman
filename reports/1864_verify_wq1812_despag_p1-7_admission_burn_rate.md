# wq#1864 — Adversarial verify-pass of wq#1812 (despag P1-7: account-global Max admission gate — burn-rate reserve, governor can-dispatch, LIMIT wall, usage poll)

**Verdict: CONTRADICT** — `despag/admission.py` (commit `2fe67a9`, PR #546) wires the
LIMIT wall, `governor_core.evaluate_dispatch`, and `burn_rate.allowed_concurrent`
correctly for the case its own tests cover, and all 132 despag tests (11 new + 121
pre-existing) pass. But the burn-rate concurrency ceiling — the exact mechanism the
ticket is named for ("burn-rate reserve") and the one piece the ticket text calls out
twice as "never a bypass" — silently becomes a full bypass whenever
`burn_rate.allowed_concurrent` cannot compute a rate (`(None, "no usable
percent+resets_at in ...")`), which happens whenever `resets_at` is missing/expired
for every pool a model draws on. `DESPAG_MAX_CLAUDE_CONCURRENT`, the gateway's own
documented upper clamp, is never consulted in that branch. This is not a hypothetical
mock; the same `burn_rate.allowed_concurrent` function's only other caller
(`dispatcher/foreman_dispatcher.py`) already treats a `None` return as "fall back to
the tight_mode clamp," per that function's own docstring ("the caller falls back to
the binary tight clamp rather than trusting a guess") — `admission.py` is the caller
that didn't follow the contract its own imported dependency documents.

## What was shipped

`despag/admission.py` (new, 265 lines): `check_admission(model, running_claude_max,
now)` stacking (1) an account-global in-process LIMIT wall (`_paused_resets_at`,
`LimitObserver` wrapping `classifier.RunState`), (2) `governor_core.evaluate_dispatch`
fed floors/state/stop/live_blocks read via `foreman_usaged`, and (3)
`burn_rate.allowed_concurrent` for the FRANK_RESERVE_PCT-aware concurrency ceiling,
clamped to `DESPAG_MAX_CLAUDE_CONCURRENT`. `despag/app.py` wires it into
`create_response` before idempotency/session-lease state is touched, returns `429
lumen_max_window_exhausted` (LIMIT wall, with `Retry-After`) or `429 quota_exhausted`
(governor/burn-rate refusal), and adds a `lumen.budget` block to `GET /v1/uhp`.
`despag/runner/turn.py` adds `Turn.model` and `TurnRegistry.count_in_flight` so the
ceiling counts real in-flight turns. `despag/tests/test_admission.py`: 11 tests
(reserve honored, LIMIT wall account-global, stale/unreadable state fails closed,
governor-raises fails closed, litellm exempt, budget snapshot shape).

## Reproduced live

- `python3 -m pytest despag/tests/ -q` → **132 passed** (this worktree, no changes).
- Confirmed every function signature `admission.py` calls
  (`governor_core.evaluate_dispatch`, `foreman_usaged.load_floors/load_state/
  load_events/_daemon_stale/STOP_PATH`, `burn_rate.merge_config/load_observed/
  pools_for_model/allowed_concurrent`) matches the real imported modules — no
  drift, no missed rename.

## The contradiction, reproduced against the real module (not a mock)

```
$ python3 probe_admission.py
running=0  -> {'allow': True, 'reasons': ['no usable percent+resets_at in session,weekly_all'], 'retry_after_s': None, 'resets_at': None}
running=500-> {'allow': True, 'reasons': ['no usable percent+resets_at in session,weekly_all'], 'retry_after_s': None, 'resets_at': None}
```

Setup: real `despag.admission` module, governor state forced non-stale with
`floors` zeroed (the fleet's actual current floors.json setting per the PR's own
report) and `limits` carrying `percent` but no `resets_at` on `session`/`weekly_all`
(a real shape: right after `foreman_usaged`'s poller restarts and repopulates
`percent` before `resets_at`, or the upstream API omits it for a cycle — the same
condition `dispatcher/burn_rate.py`'s own docstring calls out as the reason
`allowed_concurrent` returns `None` instead of a number). `evaluate_dispatch` allows
(zeroed floors, not stale, no live_blocks — wq#1737's own carve-out), so admission
reaches `burn_rate.allowed_concurrent`, which returns `(None, "no usable
percent+resets_at...")`. `admission.py:226`'s guard is
`if allowed is not None and running_claude_max >= allowed:` — when `allowed is None`
this is always `False`, so **the concurrency ceiling never engages, at any value of
`running_claude_max`.** 500 turns already running is still admitted. This is a full
bypass of gate 3, exactly the thing `despag/admission.py`'s own docstring (lines
20-24) and wq#1812's ticket text both say must never happen: "clamped to
DESPAG_MAX_CLAUDE_CONCURRENT as an upper bound only, never a bypass."

`DESPAG_MAX_CLAUDE_CONCURRENT` is passed into `allowed_concurrent` only as its
`ceiling` parameter (`burn_rate.py:141`: `int(min(max(best, cfg["min_allowed"]),
ceiling))`), which is unreachable code when `best` stays `None` — so the "upper
clamp" the docstring promises is never applied in this branch either. Grepped the
whole `despag/` tree: `DESPAG_MAX_CLAUDE_CONCURRENT` has no other reference, so there
is no independent absolute cap anywhere in the gateway.

## Why the tests didn't catch it

None of `test_admission.py`'s 11 tests constructs a `limits` dict missing
`resets_at` (or with it expired/out-of-window) on every pool `pools_for_model`
returns. Every reserve/concurrency test (`test_reserve_honoured_remaining_below_
reserve_refuses`, `test_reserve_not_yet_breached_allows_more_than_one`) supplies a
complete `resets_at` on both `session` and `weekly_all`, so `burn_rate.
allowed_concurrent` always returns a real integer in the suite. The `allowed is
None` branch of `admission.py:226` has zero test coverage.

## Why this matters for the ticket specifically

wq#1812's own acceptance text: "the gateway's own DESPAG_MAX_CLAUDE_CONCURRENT is
only an upper clamp, never a bypass; count in-flight claude-max turns from the
runner." The shipped code satisfies this exactly when burn-rate data is present and
satisfies neither clause when it isn't — the ceiling isn't just loosened, it's
entirely absent, and `DESPAG_MAX_CLAUDE_CONCURRENT` (the literal clamp named in the
acceptance text) is bypassed along with it. This is despag's cost-protection gate
against draining Frank's own Max subscription (the ticket's stated motivation,
"it has already drained his session once") — the exact scenario this bug opens
(unbounded concurrent `claude -p` spawns) is the one the whole ticket exists to
prevent, and it opens precisely when governor telemetry is momentarily unreliable,
which is also when a human is least likely to be watching closely.

## Adversarial angles tried, nothing found

- LIMIT wall: `record_limit_frame`/`_limit_wall`/`LimitObserver` — account-global
  pause, correct `Retry-After` math, un-boundable `resets_at=None` fails closed
  and stays paused (matches fworker.py's own shape), rollover clears without an
  explicit reset. Reproduced with the real functions, not just the unit tests.
- `_read_governor_state` caching: `DESPAG_USAGE_POLL_SECONDS` cache correctly
  fails closed on `load_floors`/`load_state` exceptions independently (floors ->
  `governor_core.DEFAULT_FLOORS`, state -> `None`/stale), never crashes into an
  allow.
- `is_local_model`/`LOCAL_POOL_MODELS` triplicated across `fworker.py` (referenced),
  `despag/app.py`, and `despag/admission.py` — all three lists are identical
  (`("deepseek-v4-flash", "qwen36-local")`), no drift.
- Wiring order in `app.py:create_response` — admission runs before idempotency key
  and session lease are touched, confirmed by reading the handler top to bottom; a
  refusal really does need no cleanup.
- `TurnRegistry.count_in_flight` excludes `done` turns and filters on `t.model`
  correctly; `Turn.model` threads through both `REGISTRY.start` call sites
  (streaming and non-streaming).
- TOCTOU note (not filed, minor): `create_response` awaits `_idem_admit`/
  `_resolve_session`/`lease_admit` between the admission check and
  `REGISTRY.start`, so concurrently arriving requests can all read the same
  `running_claude_max` and all pass before any of them registers a `Turn`,
  overshooting the ceiling by up to the number of requests in that window. Real,
  but bounded and far less severe than the unbounded `None`-branch bypass above;
  left unfiled as noise relative to the main finding per this archetype's
  guidance.

## Bottom line

CONTRADICT. The gate is correctly built and tested for every case where burn-rate
telemetry is fully populated. It has no fallback for the case where it isn't, and
silently chooses "no ceiling" instead of the codebase's own established "tight
clamp" fallback (`dispatcher/foreman_dispatcher.py`'s `burn_allowed`), which
defeats the ticket's explicit "never a bypass" requirement for
`DESPAG_MAX_CLAUDE_CONCURRENT`. Filed `wq#1865` (`claude-opus-5@high`, deps
`wq#1812`) to fix — likely: when `burn_rate.allowed_concurrent` returns `(None,
detail)`, clamp `running_claude_max` against `DESPAG_MAX_CLAUDE_CONCURRENT`
directly (mirroring the dispatcher's tight-mode fallback) rather than skipping the
check, plus a test constructing `limits` with `percent` but no `resets_at` on every
applicable pool and asserting refusal once `running_claude_max` reaches
`DESPAG_MAX_CLAUDE_CONCURRENT`. Not re-implemented here per this archetype's
instruction.

## ISSUE-LOG

Appended as issue entry (see `/home/lumen/kb/ISSUE-LOG.md`): `admission.py`'s
burn-rate concurrency ceiling silently no-ops (unbounded) instead of failing tight
when `burn_rate.allowed_concurrent` returns `None` for missing/expired
`resets_at`; solution deferred to wq#1865.
