# wq#1758 — adversarial verify-pass of wq#1728 (PR#456)

**Verdict: CONFIRMED.** Every quantitative claim in
`reports/1728_verify_wq1699_burn_rate_first_tight_pass.md` was independently
re-derived from `dispatcher.log` and the cited source files; all reproduce. One
citation error found (does not change any finding).

## Method

Re-ran each check cold, without trusting the report's numbers:

- Extracted the post-relogin window (`dispatcher.log:67898`-end, now 75717 lines —
  the log grew ~2800 lines since wq#1728 closed, but zero new `tight_mode` events
  landed in that growth) and independently grouped events by `pass_id` in Python to
  recheck the "every `tight_mode` pass has an `api_severity` line" claim: **40/40,
  zero misses** — matches.
- Re-tallied `burn_ceiling` values on every `pass_end` in the window and across the
  *entire* log: only `{}` (171) or
  `{"session,weekly_all,fable_weekly_scoped": null}` (46, 40 of them in-window) —
  never a real number, anywhere in history. Also independently confirmed
  `grep -c '"event": "burn_rate"'` and `grep -c "burn-rate ceiling"` are both `0`
  across the full log — the scaled path has never fired live, not once, which is
  stronger direct evidence for the wq#1754 gap than the report cited.
- Confirmed zero `tight_pri` skip events in the window (matches).
- Spot-checked every `dispatcher.log` line citation (`67892`, `67898`, `67899`,
  `68002`, `68433`, `68544`, `72916`, and the `fdisp-20260911T011236Z-1720248`
  pass) byte-for-byte against the live file — all correct, including the
  mid-pass-transition explanation for `spawned:5` against a 4-max clamp (verified
  the 5 spawns are all `claude-sonnet-5`/`fleet-fires`, logged *before* the
  `tight_mode` flip triggered by a later `claude-fable-5` candidate in the same
  pass — not a violation).
- Checked every code citation against the current file content:
  - `dispatcher/burn_rate.py:112-135` (`allowed_concurrent`) — exact match.
  - `foreman_dispatcher.py:1411-1417` (old binary clamp), `:1427-1431` (tight-limit
    enforcement gated on `tight` flag), `:1465-1467` (burn-rate halt emit) — all
    exact match.
  - `dispatch_health.py:176-177` (`"no_slots" in reason` → healthy) — exact match.
  - `governor/floors.json:2-5` (all floors + `soft_margin_pct` = 0) — exact match.
  - `dispatcher/config.json` `tight_mode.min_pri=1`, `burn_rate.min_allowed=1`,
    `avg_worker_pct_per_hour.fable_weekly_scoped=0.75` — exact match, and the
    hand-computed `0.63%/h / 0.75%/worker-h ≈ 0.8 → allowed=1` sanity-checks
    against this config.
  - `foreman_dispatcher.py:581-593` (`governor_limits`) — exact match.
  - **`orchestrator/router.py:808-813`** (cited for "only returns tight on
    severity==critical when floor==0") — **wrong**. Lines 808-813 in the current
    file are the `# ---- wq glue ----` section divider and the `WQ_TIMEOUT_S`
    constant (wq#1408 comment), not `governor_pressure`. The actual
    critical/floor check the report describes is at lines 803-805
    (`if sev == "critical" or (floor > 0 and sev == "warning"): return "tight"`),
    inside a function spanning roughly 750-806. The described *behavior* is
    correct — I re-read the real lines and the logic matches exactly what's
    claimed — only the line numbers are off by ~5-8 lines. This worktree's HEAD
    is the exact commit wq#1728 verified (1b5b0a2, no later commits), so this
    isn't drift from a later edit; it's a citation slip at authoring time.
- Confirmed wq#1754 (`/home/lumen/bin/wq show 1754`) exists, is `claimed` (not
  lost/dropped), and its `detail` field matches the report's gap description and
  line refs.

## Disposition

One real defect found — a wrong line citation in `router.py` (808-813 instead of
~799-806) — but it doesn't touch any of the report's substantive findings, which
all independently reproduce, including the central wq#1754 gap (now with stronger
evidence: zero `burn_rate` events and zero "burn-rate ceiling" halts anywhere in
`dispatcher.log`'s full history, not just the examined window). Not escalating to
opus-5@high: this is a citation-precision nit, not a contradiction of any claim or
verdict. Logged in `/home/lumen/kb/ISSUE-LOG.md` for the record.
