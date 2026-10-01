# wq#3158: Workers Logs Commander-drop attribution (48h after the wq#2925 deploy)

**Finding:** I couldn't run the path-level Workers Logs query. The deploy token has no
read access to it. The Worker-side data that token can read (Account Analytics) supports
the wq#3264 RCA: drops come from the 70 s `tools/call` abort, and nothing points at the
OAuth path. I made no worker change because nothing showed the KV-lag `/authorize` 400
this ticket asked about.

Window: 2026-09-29T19:02:00Z to 2026-10-01T19:02:00Z. Checked live at 2026-10-01 19:06:46
UTC (epoch 1790881606, 4.8 min past the liveness epoch 1790881320).

## 1. The Workers Logs query is blocked by missing read access, not by the pipe

`python3 reports/wq3158/cf_obs_probe.py` with `~/.cf_workers_token`:

```
scripts 200 ['lumen-chrome-mcp', 'lumen-deadman', 'lumen-mail-probe', 'lumen-mailbot', 'lumen-mcp', 'lumen-mcp-v2']
telemetry/query 403 [(10000, 'Authentication error')]
telemetry/keys 403 [(10000, 'Authentication error')]
```

Zone `httpRequestsAdaptiveGroups` (path plus edge status) is also refused. The token lacks
`zone.analytics.read` on both zones (`cf_gql_probe.py`). No wrangler OAuth login exists on
Lumen (`~/.config/.wrangler/config/default.toml` and `~/.wrangler/...` are both absent).
The audit-final note in `reports/wq3264_mcpw_commander_drops_rca.md:94` already flagged
this 403. It still holds.

Logging itself is on. `cf_obs_settings.py` shows `observability.enabled=true`,
`logs.persist=true`, `head_sampling_rate=1` on both `lumen-mcp` and `lumen-mcp-v2`. That
survived the five `lumen-mcp` redeploys on 2026-10-01 (02:25 to 03:33 UTC). The data
exists. Only reading it is blocked.

So these three asks are still unanswered at path level: status>=400 by path; `/register`
vs `/authorize-complete` vs `/token` 200 counts; `/mcp` 401 `invalid_token`. Getting them
needs a token with **Workers Observability Read** (plus Zone Analytics Read for the
edge-side cross-check), or someone running the query in the dashboard. Minting that token
is an act on Frank's account and is filed as a physical task (see the closing notes). Workers
Logs retention is short (3 days on Free, 7 on Paid), so the start of this window begins
aging out around 2026-10-02 19:02 UTC on Free.

The 401 vs restart correlation can't be done with Workers Logs at all. The wq#2918 full
restart (2026-09-28 01:50:57 PDT = 08:50:57 UTC) happened 34 h before observability was
deployed (2026-09-29 19:02 UTC). Nothing was logged then.

## 2. What the Worker-side analytics show (Account Analytics Read works)

`workersInvocationsAdaptive`, 48h window (`cf_gql_probe.py`):

| script | success | clientDisconnected | scriptThrewException |
|---|---|---|---|
| lumen-mcp | 9098 | 35 | 10 |
| lumen-mcp-v2 | 282 | 0 | 2 |

**Exceptions are deploy artifacts.** Every exception minute (`timeline_48h.txt`) falls in
a deploy burst: `lumen-mcp` 2026-10-01 02:27 to 02:29 and 03:32 to 03:34 (deploys at
02:25:22, 02:26:25, 02:27:16, 02:27:23, 03:33:53), and `lumen-mcp-v2` 17:19 (deploys at
17:18:38 to 17:21:12). One outlier sits at 2026-09-29 22:10 on `lumen-mcp`, and one
`lumen-mcp-v2` exception at 16:00 has no matching deploy among the 6 most recent listed.

**`clientDisconnected` is a periodic ~1.5 s client, not a drop.** All 35 have a wall time
of 0.7 to 2.2 s. They recur on a ~30 min cadence (for example 02:19, 02:50, 03:21, 03:51,
04:22 ...). A 70 s abort would show as a 70 s wall time, so these aren't Commander drops.
That pattern looks like a periodic prober with a short client timeout. The source isn't
identified here.

**The 70 s abort is visible Worker-side and agrees with wq#3264.** The analytics have no
wall-time filter, so `cf_gql_minutes.py` groups `status=success` by minute. Any minute
whose p99.9 wall time is at least 60 s contains at least one long call, which makes the
count a lower bound:

```
window 2026-09-30T02:41:12Z .. 2026-10-01T02:41:12Z   (the wq#3264 fixture window)
minutes_with_long_call 54 {'70s': 24, '75s': 7, '60-69s': 23}
48h window: minutes_with_long_call 72 {'70s': 25, '75s': 16, '60-69s': 31}
p999_seconds_hist (48h) {60: 5, 61: 17, 62: 3, 65: 1, 66: 5, 70: 25, 75: 16}
```

Over the same window, wq#3264 counted 36 cancel seconds on `:3100`, with 31 of them having
a call aged 66 to 72 s in flight. The Worker shows 24 + 7 = 31 minutes containing a call of
at least 70 s. The two sides of the tunnel agree. These calls end as `status=success`
because the Worker catches its own `AbortSignal.timeout` and responds. That's also why the
drops never appeared as Worker errors.

There are two plateaus, 70.x s and 75.x s. 70 s is `TOOLS_CALL_TIMEOUT_MS`. I couldn't
attribute the 75 s plateau: the worker source (`/home/lumen/projects/mcp-oauth-worker`) is
outside this worker's read grant, and the live worker has diverged from that tree anyway
(wq#3172). The 60 to 61 s cluster is consistent with callers that cap their own wait near
60 s (for example `lumen-run wait`) and return normally.

## 3. KV-lag `/authorize` 400 retry: not applied

The ticket made the `lookupClient` retry conditional on seeing `/authorize` return 400
unknown client after `/register`. That evidence needs the path-level logs from section 1,
and they're unreadable. Every drop this window has a mechanism (section 2 and wq#3264).
Adding speculative retry code to an auth path, on a worker whose live version has diverged
from source, would be winging it. So I committed no worker change.

## Re-run

All probes read `~/.cf_workers_token`, print aggregates only, and never echo the token:
`python3 reports/wq3158/cf_obs_probe.py`, `cf_obs_settings.py`, `cf_gql_probe.py`,
`cf_gql_timeline.py <since> <until>`, `cf_gql_long.py`, `cf_gql_minutes.py <since> <until>`.
The probe that checks this finding is `cf_obs_probe.py`. Its `telemetry/query` line flips
from 403 to 200 once a token with Workers Observability Read is in place, and then the
path-level query can run. `timeline_48h.txt` is the captured run of the timeline probe.
