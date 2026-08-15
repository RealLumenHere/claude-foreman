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
  active floor breaches, off-peak flag.
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

Defaults in `floors.json` (session >=20% remaining, weekly_all >=15%
remaining, Fable weekly_scoped >=50% remaining reserved for Frank's
interactive use). Edit that file to tune; the daemon re-reads it every poll.

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

23 cases covering floor logic (allow/deny/boundary), the Fable-only reserve
gate, the STOP kill-switch, fail-closed behavior on stale/missing data, live
`rate_limit_event` blocking, and the off-peak flag.

## Known gap

CCC fallback is implemented but not provisioned — no claude.ai session
cookie is available on this box. If the primary `/api/oauth/usage` poll
starts failing, the daemon will fail closed to local-rung-only after 10
minutes rather than silently degrading, which is the safe default, but it
means the CCC fallback path is currently untested against a real cookie.
