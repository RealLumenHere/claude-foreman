# foreman-usaged

Budget governor daemon for the claude-foreman build fleet (wq#613, BUILD_PLAN.md §T1).

Polls `GET /api/oauth/usage` every 60-90s using the CLI's own OAuth bearer
(`~/.claude/.credentials.json`), refreshing via `claude auth status` (zero
quota) on a 401. Falls back to a CCC (claude.ai cookie) poll after 2
consecutive primary failures — currently unprovisioned on this box (no
session cookie configured; see `CCC_SESSION_COOKIE_FILE` in
`foreman_usaged.py`). Fails closed to local-rung-only if both sources are
stale for more than 10 minutes.

On consecutive primary failures the poll interval backs off (doubles per
failure, capped at `poll_backoff_max_s`, default 600s) instead of retrying at
the flat 60-90s cadence — wq#639 found a 2.5h+ continuous HTTP 429 streak on
the usage endpoint where the un-backed-off daemon kept polling every ~70s the
whole time, plausibly re-arming a sliding-window rate limit and extending the
fail-closed (stale) window well past what the poll cadence should allow.

## State

- `/home/lumen/foreman/state.json` — atomic snapshot of the last poll:
  parsed `limits` (session / weekly_all / Fable weekly_scoped), staleness,
  active floor breaches, off-peak flag. `cache_age_s` is how old the served
  `limits` are (seconds since the last *good* read) — on a 429 or any other
  failed poll the last-good `limits` are served as-is, annotated with this
  age (`reused_cache`/`reused_cache_reason`); `stale` (fail-closed to
  local-rung-only) only trips once `cache_age_s` exceeds
  `stale_fail_closed_seconds` (default 600s), never on one bad read alone
  (wq#648 / RELIABILITY_SPEC.md §B — the fworker bug 1 root cause).
- `/home/lumen/foreman/STOP` — kill-switch. Presence of this file (any
  content) halts all cloud dispatch immediately; delete it to resume.
- `/home/lumen/foreman/events.json` — live rejected `rate_limit_event`
  frames a worker harness reports in-band (`report-event`), which override
  the poll cadence immediately.

## CLI

```
foreman_usaged.py run [--once]        # poll loop, or a single poll + exit
foreman_usaged.py can-dispatch MODEL  # exit 0 = allow, 1 = deny; prints JSON decision
foreman_usaged.py status              # print current state.json
foreman_usaged.py report-event JSON   # record a live rejected rate_limit_event frame
```

`can-dispatch` is the harness-facing contract: any process about to spawn a
cloud `claude -p` worker should call it first and treat a non-zero exit as
"local rung only, do not spawn."

## Reserve floors

Current `floors.json`: `session_min_remaining_pct`, `weekly_all_min_remaining_pct`,
and `fable_weekly_min_remaining_pct` are all **0**, and off-peak throttling is
disabled (`off_peak_days_pt: []`). Edit that file to tune; the daemon re-reads
it every poll.

**Why they're zeroed, not tuned.** wq#734/SESSION_ECONOMICS.md §3/§8 (wq#738)
found the previous floors were denominated in the wrong window: across 39
fleet transcripts spanning four days, the 5-hour window never once produced a
utilization reading (all 7 `five_hour` `rate_limit_event` frames carry
`status: allowed` with no utilization field), while the 7-day window walked
62% -> 80%, 18 net points in 77.9 hours — the window the old
`session_min_remaining_pct: 20` floor guarded never bound, and
`weekly_all_min_remaining_pct: 15` guarded the one that actually moves. wq#734
priced that window at >=$16.32 of API list price per point, with a median
build session costing <=0.97 points — so the old 15-point weekly floor was
roughly 15 median build sessions of reserve, held back from a window that, per
Anthropic's own error reference, can be exhausted by "a single burst of heavy
activity" regardless of session-window state.

wq#738 put a re-tune of both numbers to Frank with that exchange rate in
hand. Frank's actual answer, given directly (not through the ticket) and
applied across 2026-08-21/22/23, went further than a re-tune: remove all
throughput throttling fleet-wide — lane caps raised to 999, reserve floors
zeroed, off-peak disabled (commit `890afe3`, refs wq#715). The only remaining
real limit is `cloud_ceiling=3` concurrent, the account's own session cap.
**Do not restore non-zero floors without a new, explicit Frank instruction.**

`fable_weekly_min_remaining_pct` is a single static number. For a Frank-ruled
time-stepped reserve (e.g. "keep >=10% through Tuesday, may burn to 0% from
Wednesday evening"), add an optional `fable_floor_schedule`: an ordered list
of `{until_pt, min_remaining_pct}` steps in Pacific wall-clock time
(`"YYYY-MM-DDTHH:MM"`, no offset). The first step whose `until_pt` is still
in the future is active; once every step has passed — or the key is absent,
empty, or any step is malformed — `resolve_fable_floor()` falls back to the
plain static key. `evaluate_dispatch` and `floor_breaches` both resolve
through it, so a schedule change takes effect on the very next poll/dispatch
without a daemon restart.

## Running

Detached via lumen-run (survives the DC-gateway process-group reap):

```
lumen-run start --name foreman-usaged --mem 512M -- \
  python3 /home/lumen/foreman/governor/foreman_usaged.py run
lumen-run status <UNIT>   # RUNNING | DONE:0 | FAILED:<code> | GONE
lumen-run log <UNIT>      # daemon's stdout/stderr (structured log lines)
```

## Tests

```
cd /home/lumen/foreman/governor && python3 -m pytest test_governor_core.py -v
```

36 cases covering floor logic (allow/deny/boundary), the Fable-only reserve
gate and its optional time-stepped schedule, the STOP kill-switch,
fail-closed behavior on stale/missing data, live `rate_limit_event`
blocking, and the off-peak flag.

## Undocumented pool: `seven_day_overage_included` (wq#739)

The live `rate_limit_event` frame the CLI emits carries a `rateLimitType` that
is not just `"five_hour"|"seven_day"`. CLAUDE_CLI_INTERNALS.md §D, decompiled
from the CLI binary [SRC BuE/juE], gives the full enum:

```
rateLimitType: "five_hour"|"seven_day"|"seven_day_opus"|"seven_day_sonnet"|
               "seven_day_overage_included"|"overage"
```

`seven_day_overage_included` was observed live on one Fable session in the
wq#734 corpus (`fable617-268843.jsonl`, 3 frames, all `status:
"allowed_warning"`, `surpassedThreshold: 0.75`, utilization 0.81-0.83) at the
same wall-clock minutes the plain `seven_day` pool on other sessions read
0.69-0.70. It has not been seen anywhere else in the corpus (49 `seven_day`
frames, 7 `five_hour`, only these 3 `seven_day_overage_included`, all one
session).

**What it credibly is:** the enum sits `seven_day` / `seven_day_overage_included`
/ `overage` in the same list — read together with the frame's own
`overageStatus`/`overageDisabledReason`/`isUsingOverage` fields, the shape
implies `seven_day_overage_included` is the seven-day utilization *as it
would read if the account's extra-usage credits were counted into the same
window* (i.e. base subscription usage + overage spend, combined), separate
from the plain `seven_day` figure which is subscription-only. The
`allowed_warning` status and `surpassedThreshold: 0.75` on every observed
frame fit a warning pool: it fires as a heads-up once the combined figure
crosses 75%, distinct from the hard-wall pools. This is inference from the
decompiled enum and the frame shape, not a documented guarantee — Anthropic's
statusline reference names only `five_hour` and `seven_day`.

**Confirmed gap: `/api/oauth/usage` has no matching entry.** PROBE_RESULTS.md
§P3a captured the live payload shape: `limits[]` entries are `kind ==
"session"|"weekly_all"|"weekly_scoped"` only, plus top-level `five_hour`,
`seven_day`, `seven_day_opus`, `seven_day_sonnet`, `seven_day_oauth_apps`,
and a separate `extra_usage` object (`is_enabled`, `monthly_limit`,
`used_credits`, `utilization`, `disabled_reason`). No
`seven_day_overage_included` key anywhere in that payload. `parse_limits()`
above only reduces `limits[]` by `kind`, so it was never going to see this
pool even if present — it lives exclusively in the push-stream
`rate_limit_event` frames, not the poll snapshot the governor reads.

**Why this matters if extra-usage credits are ever turned on:** the governor
polls `/api/oauth/usage` and floors against `session`/`weekly_all`/Fable
`weekly_scoped`. If Frank enables usage credits, dispatch could still get hit
with a live `rejected` on `seven_day_overage_included` (harness workers
already report live rejected frames via `report-event`, see State above) that
the *poll* path has no visibility into and no floor for — the poll snapshot
would look fine while the push stream reports the wall. Today this is inert:
every frame in the corpus carries `overageStatus: "rejected"`,
`overageDisabledReason: "out_of_credits"`, so the pool can never bind. Status:
UNKNOWN beyond the above — not verified against Anthropic's source, just the
decompiled enum + one session's live frames. Re-open if extra-usage credits
get enabled.

## Known gap

CCC fallback is implemented but not provisioned — no claude.ai session
cookie is available on this box. If the primary `/api/oauth/usage` poll
starts failing, the daemon will fail closed to local-rung-only after 10
minutes rather than silently degrading, which is the safe default, but it
means the CCC fallback path is currently untested against a real cookie.
