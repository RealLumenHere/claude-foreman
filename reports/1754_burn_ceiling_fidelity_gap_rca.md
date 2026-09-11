# wq#1754 — RCA: burn_ceiling never non-null, and why severity/percent+resets_at disagree

**Verdict: root cause identified by code inspection; gap has since self-resolved and is now
confirmed live in production (29/29 non-null `burn_ceiling` passes since 2026-09-11T04:06:30Z,
including a real burn-rate-gated halt). No code change needed — behavior is the intended
fail-soft design, not a bug.**

## The question

wq#1728 found that `burn_rate.allowed_concurrent` (`dispatcher/burn_rate.py:112-135`) had
never returned a real value in `dispatcher.log`'s history, including through the first live
tight window after the 2026-09-10 Max re-login (23:32:33Z-02:35:37Z), despite
`api_severity` showing valid `critical`/`warning` labels the whole time. Asked: why can
`severity` and `percent`/`resets_at` disagree, and does `burn_ceiling` ever go non-null live?

## Root cause: two consumers, two validity rules, same underlying entry

`governor_limits()` (`dispatcher/foreman_dispatcher.py:581-593`) reads one `limits` dict
straight from `state.json`, and both consumers read the *same* dict — there is no
separate/different-fidelity source:

- `orchestrator/router.py:governor_pressure` (:796-813) only requires `percent` to be
  non-`None` to reach the severity check (:798-801 `continue`s past a pool with no percent
  on a zero-floor pool, but never checks `resets_at` at all). A stale-but-present
  `severity` label is trusted with no freshness gate.
- `dispatcher/burn_rate.py:pool_spend_rate` (:101-119) requires **both** `percent` and
  `resets_at`, and additionally requires `resets_at` to parse and land in
  `(now, now + WINDOW_S[pool]]` (:112-114) — by design ("never guess", module docstring
  :21-23). A `resets_at` that has already elapsed, or is unparseable/missing, invalidates
  that pool's spend-rate math even though `percent`/`severity` in the same dict entry are
  still read as valid by `governor_pressure`.

So the "different-fidelity" framing in the ticket is really: **one consumer has a
staleness/validity gate on the time-relative field, the other doesn't gate the label at
all.** When `state.json`'s `limits` blob is stale (see below), `severity` (a static label)
keeps reading as `critical`/`warning` while `resets_at` (a clock-relative field) ages out of
`pool_spend_rate`'s validity window — producing exactly the observed disagreement.

## Why `limits` was stale for ~3h post-relogin

`governor/foreman_usaged.py:fetch_primary` (:102-121) documents that `/api/oauth/usage`
"429s aggressively under polling (#31021/#31637) and may not recover for hours" — this is
also called out as the expected steady state in `research/RELIABILITY_SPEC.md:24` ("treat
429 as reuse-cache, never fail-closed on one stale read"). `poll_once` (:228 onward) reuses
`prev_state["limits"]` verbatim on any failed poll (:250-251). The frozen, byte-identical
`api_severity` **and** `burn_rate` detail strings across all 46/40 occurrences in the
23:32:33Z-02:35:37Z window (dispatcher.log:67899-:72916, confirmed in the wq#1728 report) is
the signature of exactly this: one stale `limits` snapshot served unchanged, poll after
poll, through a post-relogin 429 storm. `resets_at` in that snapshot fell out of
`pool_spend_rate`'s validity window as real time advanced past it; `severity` kept reporting
off the same frozen snapshot with no such check.

## Confirmed: burn_ceiling has gone non-null live, and it gates

Re-checked `dispatcher.log` fresh for this ticket (2026-09-11, current):

- Last `null` burn_ceiling: `dispatcher.log:72918` (pass `fdisp-20260911T023501Z-2060090`,
  02:35:37Z) — matches the wq#1728 window's documented end.
- `api_severity` then went quiet for over an hour (no hot pools, pressure `ok`) and resumed
  at 03:53:42Z.
- **First live non-null burn_ceiling**: `dispatcher.log:79565-79567`
  (pass `fdisp-20260911T040606Z-3989776`, 2026-09-11T04:06:30Z):
  ```
  {"event": "burn_rate", "model": "claude-sonnet-5", "reason": "pressure tight: burn-rate ceiling 1",
   "detail": "session: 4.04%/h / 1.50%/worker-h = 2.7; weekly_all: 0.51%/h / 0.35%/worker-h = 1.5"}
  {"event": "halt", "stage": "concurrency", "reason": "no_slots",
   "detail": "9 cloud unit(s) at burn-rate ceiling 1; next tick retries"}
  ```
  This is the wq#1728 checklist item #4 (a live burn-rate stop, classified healthy by
  `dispatch_health.py:176-177`'s `"no_slots" in reason` rule) now observed directly, not
  just confirmed by inspection.
- From 04:06:30Z through the most recent `burn_rate` event at 06:19:24Z: **29/29**
  `pass_end.burn_ceiling` entries are non-null (`{"session,weekly_all": 1}`); **zero** null
  entries anywhere after 02:35:37Z (`grep -c` over the post-window log). The controller has
  stayed healthy since its first real snapshot arrived — the gap was transient, tied to the
  post-relogin 429 storm, and did not recur.

## Conclusion

Not a defect: `burn_rate.py`'s stricter validity check on `resets_at` is deliberate
("never guess" a spend rate off stale timing data), `governor_pressure`'s severity check was
never meant to gate on freshness (it predates wq#1699 and only grades the label), and the
observed disagreement is fully explained by a known, documented 429-storm caching behavior
that self-heals once a fresh poll lands. `burn_ceiling` going non-null in production is now
directly confirmed, including a real gating halt. No code change filed against this ticket;
closing as verified.
