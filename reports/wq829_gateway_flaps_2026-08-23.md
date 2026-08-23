# wq#829 — Commander/MCP gateway flaps under parallel-chat fan-out

**Date:** 2026-08-23 · **Worker:** wq829-a1 (headless BUILD) · **Status:** root-caused; fix prepared in `ops/wq829-mcp-gateway/`; apply + test-to-failure blocked on root and a quiet window (see "What remains").

## TL;DR

- `mcp-pressure-guard` is **not broken and never was**. It is a `Type=oneshot` service fired by a once-a-minute timer; `inactive (dead)` is its normal idle state. It has run every minute for 2 weeks and tripped correctly during Frank's run.
- The "Commander is down" flaps are the **guard's own remedy**: under fan-out the gateway's per-request child spawns drive memory PSI past the trip threshold, and the guard's `systemctl restart` produced a **2-minute hard outage** because the stop phase hung on a `restic` job stuck in the gateway's cgroup.
- Fix: cap concurrent child spawns inside supergateway (admission gate + queue + 503 shed), bound unit stop time to 10s, keep the guard as a backstop. Prepared, not yet applied.

## Root cause chain (all timestamps 2026-08-23 UTC, from journald)

1. **Per-request child model.** `desktop-commander-mcp.service` runs supergateway 3.4.3 in **stateless** streamable-HTTP mode (`--outputTransport streamableHttp`, no `--stateful`). In this mode supergateway **spawns a fresh `desktop-commander` Node process for every HTTP POST** and reaps it (SIGTERM, then a SIGKILL sweep) when the response closes (`stdioToStatelessStreamableHttp.js:133,151`). The constant `Child exited: code=null, signal=SIGTERM` journal lines are this reap — normal operation, not the failure.
2. **Fan-out → spawn storm.** Frank's parallel-chat run drove up to **29 requests/minute** (02:40–03:07 window). With 15–30s tool-call lifetimes that is ~10+ concurrent Node processes plus their spawned workloads, all inside one cgroup capped at `MemoryHigh=8G` (historical peaks: 8.8G memory, **17.2G swap**). Reclaim pressure stalls the whole box: PSI `full avg60` hit **50.37**.
3. **Guard trips, remedy is the outage.** 03:07:06 `mcp-guard: TRIP psi_full_avg60=50.37 — restarting desktop-commander-mcp`. The restart's stop phase hung **2m06s**: a `restic` backup (started through desktop-commander, never detached, so it lived in the unit cgroup) ignored SIGTERM until systemd's `final-sigterm` timeout SIGKILLed it — also killing the backup mid-run. Gateway hard-down 03:07:06→03:09:13. Every chat mid-call saw a dead pipe → "Commander is down". Post-restart supergateway re-initialized cleanly, which is exactly the confusing "MCP fully initialized (but chats saw it die)" signature in the ticket.
4. **Secondary mid-call kill path.** `OOMPolicy=continue` + `MemoryMax=12G` means the kernel can OOM-kill the fattest child mid-request; supergateway then surfaces a 502 to that one chat (`#139` handler). Same user-visible symptom, smaller blast radius.

### Why "mcp-pressure-guard won't start" was a misdiagnosis

`systemctl status mcp-pressure-guard` always shows `Active: inactive (dead)` because a oneshot triggered by `mcp-pressure-guard.timer` only lives for the ~25ms it takes to run. The timer (`enabled`, active since 2026-08-08) fires it every minute; trips are logged under syslog tag `mcp-guard`. Check it with `systemctl status mcp-pressure-guard.timer` and `journalctl -t mcp-guard`, not the service's Active state. The guard did exactly what wq#380 designed it to do — the design flaw is that its only remedy (whole-unit restart) is itself a multi-minute outage when the cgroup contains SIGTERM-ignoring children, and that it treats a load problem as a restart problem.

## Fix (prepared in `ops/wq829-mcp-gateway/`)

| Ticket item | Change | Artifact |
|---|---|---|
| (2) survive N concurrent clients | Admission gate in the stateless gateway: cap concurrent children (`SUPERGATEWAY_MAX_CONCURRENT_CHILDREN`, default 12), FIFO-queue overflow, shed with `503 + Retry-After` after `SUPERGATEWAY_QUEUE_TIMEOUT_MS` (30s). Prevents the spawn storm → prevents PSI trips → the guard stops firing. **Behavior-verified in isolation** (see below). | `stdioToStatelessStreamableHttp.js` (full patched file), `gateway-patch.diff` (review diff) |
| (3) recycle without erroring clients | Queued requests ride out a child recycle invisibly (they haven't spawned yet). For unit-level recycles: `TimeoutStopSec=10` bounds the outage to a ~15s blip instead of 2m06s; the guard's cooldown (600s) prevents flap loops. claude.ai's MCP client already reconnects on the next call — the kill zone is mid-call, so shrinking the window and shedding early (503 before spawn, not death mid-response) is the available lever; we cannot patch the client side. | `wq829-fast-stop.conf` (systemd drop-in) |
| (4) safe ceiling | Measured, not guessed: `loadtest.sh` ramps N ∈ {4,8,12,16,24,32,48} parallel clients × 5 full MCP round-trips each, reports ok/shed/error/p95/PSI/cgroup-mem/guard-trips per phase, stops at first real failure, prints the highest clean N. The drop-in's `12` is provisional until this runs. | `loadtest.sh` |
| (1) guard won't start | Misdiagnosis — documented above; no code change needed. The guard stays as the backstop of last resort. | this report |
| Rollout | Root-gated apply script with built-in **quiet-window check** (refuses if the gateway served traffic in the last 10 min), backup of the original dist file, drop-in install, restart, health check, MCP smoke test. | `apply.sh` |

### Patch verification (isolated instance, no live-gateway contact)

The patch was proven behaviorally, not just syntax-checked, against a private supergateway copy in `/tmp` on port 3199 with a dummy stdio MCP child (2s per tools/list), cap=4, queue timeout=3s:

- **Normal flow intact:** single request → HTTP 200, correct SSE body, full auto-initialize flow, per-request reap unchanged.
- **Cap + queue + slot transfer:** 20 concurrent clients → exactly 4×200 at ~2.0s (wave 1), 4×200 at ~4.1s (queued, admitted when wave 1 released), 12×503 at ~3.05s (shed at queue timeout); 12 matching `Shedding request` log lines; never more than 4 children in flight.
- **No slot leak on aborts:** 8 clients aborting at 1s (mid-serve and mid-queue), then a probe request → admitted instantly (2.04s, no queue delay).

The harness earned its keep: the first version of the guard used `req.destroyed` to detect clients that hung up while queued, and that check is **true for every request** once `express.json()` has consumed the body (a fully-read `IncomingMessage` auto-destroys), so every request silently hung. A stock-file control run isolated the bug in minutes. Current version captures a `close`-listener flag before entering the queue instead. Anyone re-deriving this patch: test against a stock control first.

Also, ops guidance that falls out of the restic incident: **long-running jobs started through Commander chats must detach** (`setsid …` or `systemd-run --user …`). The in-gateway reap already spares detached processes (#108); detaching additionally moves them out of the cgroup so gateway recycles can't kill them and they can't hang the stop phase.

## What remains (and why this session stopped here)

Applying and test-to-failure require both:
1. **Root** — installing the drop-in and restarting the unit; this worker's sandbox denies sudo (correctly).
2. **A quiet window** — the ticket itself forbids touching the gateway with work in flight, and Frank's run was live through this session (gateway traffic observed at 03:46, LPP edits in flight).

Runbook for whoever applies (Frank or a root-capable window):

```
sudo ops/wq829-mcp-gateway/apply.sh        # refuses if chats are active
sudo ops/wq829-mcp-gateway/loadtest.sh     # ramps to failure, prints ceiling
# then set SUPERGATEWAY_MAX_CONCURRENT_CHILDREN in the drop-in to the measured
# ceiling, daemon-reload + restart, and re-run loadtest.sh once to confirm.
```

Rollback: restore `$DIST.bak.wq829.*` over the dist file, `rm /etc/systemd/system/desktop-commander-mcp.service.d/wq829-fast-stop.conf`, `systemctl daemon-reload && systemctl restart desktop-commander-mcp`.

## Evidence index

- Guard trip + slow recovery: `journalctl -t mcp-guard --since "2026-08-23 03:00"` (TRIP 03:07:06, health=200 logged 03:09:16).
- 2m06s stop hang + restic SIGKILL: `journalctl -u desktop-commander-mcp -t systemd --since "2026-08-23 03:06:50" --until "2026-08-23 03:10"`.
- Per-request reap SIGTERMs (normal): same unit journal, `Child exited: code=null, signal=SIGTERM` at 03:46:13/14/19/28 with no systemd stop lines.
- Fan-out rate: 387 child spawns 02:40–03:07, peak 29/min at 02:57.
- Cgroup history: `Consumed … 8.8G memory peak, 17.2G memory swap peak` (systemd, 03:09:12).
