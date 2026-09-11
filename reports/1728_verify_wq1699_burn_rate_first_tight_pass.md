# wq#1728 — verify-pass of wq#1699 (burn-rate ceiling) on the first live tight passes after the Max re-login

**Verdict: MOSTLY CONFIRMED, one real gap flagged as new ticket (filed as wq#1754).**

## Window examined

- Last `governor_deny` skip: `dispatcher.log:67892` (2026-09-10T23:27:46Z, ticket 1718).
- First pass after re-login: `fdisp-20260910T233223Z-1015666`, `pass_start` at
  `dispatcher.log:67898` (23:32:23Z), `pass_end` at `dispatcher.log:68002`
  (`"spawned": 8, "tight": false, "burn_ceiling": {}`).
- Examined every pass from there through the most recent complete pass at the time of
  this check (2026-09-11T03:12Z, 64 passes, 40 of them `tight_mode`).

## Checks against the ticket's expectations

1. **`info api_severity` whenever a pool is warning/critical** — holds. Every one of the
   40 `tight_mode` passes in the window has a matching `event":"info","reason":
   "api_severity"` line in the same `pass_id` (checked programmatically, zero misses).
   First instance: `dispatcher.log:67899`.
2. **`burn_rate` events only when pressure is tight; warning alone on an unfloored pool
   must not produce `tight_mode`/`burn_rate`** — holds for the negative case: `floors.json`
   has all floors and `soft_margin_pct` at 0 (`governor/floors.json:2-5`), so
   `router.governor_pressure` (`orchestrator/router.py:808-813`) only returns `tight` on
   `severity=="critical"` when `floor==0`, never on `warning` alone. No pass in the window
   shows `tight`/`burn_rate` triggered off a bare warning.
   **Gap:** the *positive* case never fired either. `burn_rate.allowed_concurrent`
   (`dispatcher/burn_rate.py:112-135`) returned `None` on **all 40** `tight_mode` events
   in the window (post-login `tight_mode` lines with `"no usable percent+resets_at"` =
   40/40; first at `dispatcher.log:68544`, last at `dispatcher.log:72916`).
   `pass_end.burn_ceiling` never carries a real number anywhere in `dispatcher.log`'s
   full history — only `{}` or `{"session,weekly_all,fable_weekly_scoped": null}`. So
   the scaled burn-rate ceiling from wq#1699/PR#424 has **never actually computed a
   concurrency number in production**, including on this first live tight window; every
   tight pass fell back to the old binary `tight_mode` clamp
   (`foreman_dispatcher.py:1411-1417`).
   Re-running `burn_rate.allowed_concurrent` by hand against the *current* (fresh)
   `state.json` (2026-09-11T03:12:36Z) returns a real value (`allowed=1`,
   `fable_weekly_scoped: 0.63%/h / 0.75%/worker-h = 0.8` bounded to `min_allowed=1`) —
   so the function itself is not broken; something about the governor state served
   during the ~3h tight window (23:32:33Z-02:35:37Z, `api_severity` detail frozen at the
   literal string `fable_weekly_scoped=critical,weekly_all=warning` for all 46
   occurrences, `dispatcher.log:67899` through `:72916`, no drift) made `percent`/
   `resets_at` unusable for burn-rate math even though it was usable enough to yield a
   valid severity. This needs a follow-up ticket — filed as wq#1754 below.
3. **Spawns not held to `tight_mode.max_spawns_per_pass` (4)** — N/A for this window:
   since `burn` was `None` every time, the *new* scaled path was never engaged, so this
   claim couldn't be exercised. The *old* clamp path applied instead, and correctly:
   e.g. `pass_end` for `fdisp-20260911T011236Z-1720248` shows `"spawned": 5` against a
   4-max tight clamp, but that's the documented mid-pass transition (candidates before
   the tight-triggering ticket spawn under the untightened cap; `foreman_dispatcher.py`
   :1427-1431 only enforces the tight limit once `tight` flips True) — not a violation.
4. **Any burn-rate stop logged as `halt stage=concurrency reason=no_slots` with
   'burn-rate ceiling' detail, classified healthy by `dispatch_health`** — never
   occurred in this window (burn path never engaged, see #2). The *plain* concurrency
   ceiling did fire repeatedly, e.g. `dispatcher.log:68433`
   (`"10 cloud unit(s) at ceiling 10; next tick retries"`) — same `reason":"no_slots"`
   string, and `dispatch_health.py:176-177` classifies healthy on `"no_slots" in
   reason` alone, so the burn-rate variant (`foreman_dispatcher.py:1465-1467`, detail
   `"... at burn-rate ceiling {allowed}; ..."`) would be classified the same way once it
   ever fires — confirmed by code inspection, not yet by a live instance.
5. **No `tight_pri` skips of aged tickets (`epri<=min_pri`)** — holds: zero `tight_pri`
   skip events anywhere in the post-login window (`min_pri=1` per
   `dispatcher/config.json`'s `tight_mode` block).

## New ticket filed

wq#1754: burn-rate ceiling (`dispatcher/burn_rate.py:allowed_concurrent`) has never
returned a non-`None` value in `dispatcher.log`'s full history, including through this
first live tight window post-relogin, despite valid governor severity data being
present (`critical`/`warning` labels, frozen for 3h then clearing). Needs a look at what
`governor_limits()` (`foreman_dispatcher.py:581-593`) was actually returning for
`percent`/`resets_at` on those three pools during 2026-09-10T23:32Z-02:35Z — the
severity label and the percent/resets_at fields appear to come from different fidelity
sources and can disagree.
