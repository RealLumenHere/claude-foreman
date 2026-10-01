# wq#3264 — Commander (mcpw) drops mid-command: RCA, independently re-verified

Source write-up: `/home/lumen/scratch/audit-final/RCA_mcpw_70s.md` (audit-final, 2026-10-01).
This report re-checks its two kill modes with fresh evidence: a pinned, re-runnable join for Mode B
and a reproduction of both modes on a throwaway gateway. Both hold. The fixes are already filed as
wq#3265 (lumen-run wait clamp) and wq#3266 (DC preload clamp + detach, rides wq#3229).

## One mechanism, two triggers

`desktop-commander-mcp.service` runs supergateway in stateless streamable-HTTP mode (no
`--stateful`; `systemctl show -p ExecStart`). Each HTTP request spawns a fresh desktop-commander child
in its own process group (`spawn(..., {detached: true})`,
`~/.npm-global/lib/node_modules/supergateway/dist/gateways/stdioToStatelessStreamableHttp.js:133`).
The response lifecycle drives the reap (`:157-158`):

```
res.on('close',  reapChild);   // client went away (Mode B)
res.on('finish', reapChild);   // call returned     (Mode A)
```

`reapChild` sends SIGTERM to `-pgid` at once (`:151`). After `SUPERGATEWAY_REAP_GRACE_MS` (default
2000 ms, `:12-15`) it sends SIGKILL to every process with `pgrp == pgid && sid == pgid && ppid != 1`
(`:26-71`, Lumen patch #108). Anything still in the request's group dies when the call ends,
whichever way it ends. Processes in their own session (setsid, systemd-run, or reparented to init)
are spared.

- **Mode A (call returned):** `start_process` answers "still running" when `timeout_ms` elapses. The
  response finishes and the job it started is reaped with the child.
- **Mode B (client aborted):** the Worker aborts any `tools/call` at 70 s
  (`projects/mcp-oauth-worker/src/router.ts:29` `TOOLS_CALL_TIMEOUT_MS = 70_000`; `:156`
  `signal: AbortSignal.timeout(timeoutMs)`). cloudflared logs `context canceled` on
  `ingressRule=12 originService=http://localhost:3100`. supergateway sees `close` and reaps a call
  that is still mid-flight.

## Reproduction (throwaway supergateway, :3197, stub stdio server)

Probe: `reports/wq3264/stub_mcp.py`. On every request the stub spawns `sleep 241` in the request's
group and `sleep 242` with `start_new_session=True` (setsid), and answers `tools/call` only after
10 s. The live gateway on :3100 was not touched.

| request | outcome | in-group `sleep` | setsid `sleep` |
|---|---|---|---|
| `initialize` (returns normally) | 200, result | DEAD | ALIVE |
| `tools/call`, `curl -m 3` aborts at 3 s (rc=28) | client abort mid-flight | DEAD | ALIVE |

The probe could have failed in either direction. The setsid control stayed alive in both rows, so
"DEAD" really is the reaper and not a stub or harness artifact. This also shows that wq#3266's
setsid/detach fix will get past the reaper.

Command shape (re-runnable):
`env PIDLOG=<file> SLOW_S=10 supergateway --stdio "python3 reports/wq3264/stub_mcp.py" --port 3197 --outputTransport streamableHttp --streamableHttpPath /mcp`,
then POST `initialize` / `tools/call` to `http://127.0.0.1:3197/mcp` and check `/proc/<pid>` after
the 2 s grace.

## Mode B correlation (pinned fixture, with a null baseline)

`python3 reports/wq3264/cancel_join.py analyze reports/wq3264/fixture_2026-10-01.json`
(the fixture holds timestamps and a command category only, never argv; regenerate with `snapshot`):

```
window 2026-09-30T02:41:12Z .. 2026-10-01T02:41:12Z
cancel seconds on :3100: 36; with a call in flight: 36
ANY in-flight call aged 66-72s: 31/36 (86%)
  category of that call: {'other:bash': 8, 'sleep': 8, 'lumen-run wait': 6, 'other:cd': 6, 'restic': 1, 'dc-upgrade': 1, 'cargo': 1}
newest in-flight age hist: {2: 1, 3: 1, 4: 2, 11: 2, 19: 1, 20: 1, 22: 1, 27: 1, 28: 1, 40: 4, 42: 2, 54: 1, 67: 6, 68: 11, 100: 1}
null (2000 random instants, seed 3264): with a call in flight 348, ANY aged 66-72s 4/348 (1.1%)
```

86 % of real cancels have a call aged 66-72 s in flight, against 1.1 % of random instants. That
matches the audit's 28/32 over a slightly later window. The ages land on 67-68 s rather than 70 s
most likely because `started_at` in `~/.dc-handles.db` is stamped when DC spawns the job, after
supergateway has cold-started a fresh desktop-commander child for the request. The Worker's timer
starts before all of that (inferred, not timed here). Under the "newest in-flight call" rule the audit used,
short parallel calls hide some of the long ones (hence 17/36 by that rule alone). The "any in-flight"
statistic is the fair one, and the null baseline shows the window is not an artifact of call
density.

## Ruled out (audit-final, spot-checked here)

- DC unit restarts: `NRestarts=0`, `ActiveEnterTimestamp=2026-09-30 07:24:28 UTC` (the planned
  0.2.46 upgrade).
- mcp-gateway-watchdog, mcp-pressure-guard, tunnel flaps, host load: per the audit; not re-measured.
  The mechanism above fully explains the drops without them.

## Notes for the fix tickets

- wq#3266 (b): use `setsid`/`detached` in a new **session**. `nohup` alone does not help: it ignores
  SIGHUP, not the group SIGTERM at `:151`. Moving to a new process group within the same session
  also escapes the reap, but setsid is the documented contract (`:16-24`).
- wq#3266 (a) / wq#3265: 55 s leaves about 13 s of headroom under the 70 s Worker abort as measured
  at DC (67-68 s observed).
- Raising the Worker's `TOOLS_CALL_TIMEOUT_MS` is optional margin, not a fix, and needs a clean-HEAD
  deploy: the `projects/mcp-oauth-worker` tree is dirty and the live worker has diverged (wq#3172).
- Worker-side Workers Logs (wq#3158) are no longer needed for attribution. `.cf_workers_token` still
  gets 403 on `telemetry/query` (audit); a token with Workers Observability Read is a separate gap.
