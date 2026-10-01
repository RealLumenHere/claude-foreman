# wq#3294 — Commander HA: zero-downtime control plane

Frank directive 2026-10-01: Commander does not go down. 5 multi-hour outages in 6 days,
four root causes: CF Workers recycles kill OAuth state, DC upgrades crash the service,
single point of failure on Lumen, no automatic failover.

This extends `MCP_CONTROL_PLANE_HA_DESIGN.md` (instructions/drafts) from design to an
executable plan. §3.2 (per-node DC endpoints) is already built for Bes (wq#2608) and
Anubis (wq#2607); the Worker pool code is committed and its `/pool/health` endpoint is
live (wq#2854/wq#2925, `{configured:1,healthy:1,degraded:false}`). What was missing is
items A–E below. Items B and D ship as code in this directory; A, C, E are filed as
scoped follow-ups because they need repos/hardware this lane cannot touch
(mcp-oauth-worker checkout, wrangler secrets, physical purchase).

Prior art (fleet-search 2026-10-01): Cloudflare's own blessed pattern is Load Balancing
with private origin pools over cloudflared tunnels, health-checked with auto-failover
(developers.cloudflare.com/use-cases/performance/load-balancing/,
blog.cloudflare.com/private-origins-dns-routing/); durable per-client state in Workers
belongs in KV or a Durable Object, never isolate memory
(developers.cloudflare.com/durable-objects/). The design below follows both: the Worker
pool is our "origin pool", the loop in this directory is the health-check/failover
driver we can run today without a CF Load Balancing subscription, and E moves OAuth
state to KV.

## Target architecture

```
Claude client
   └── mcpw.lumennode.uk (CF Worker: OAuth + pool router; state in KV, not memory [E])
         ├── origin 1: Lumen DC   (cloudflared ingress → localhost:3100)   [live today]
         ├── origin 2: Bes DC     (mcp-bes.lumennode.uk → localhost:3100)  [wq#2608]
         └── origin 3+: P1 Pros   (same §3.2 recipe)                       [C, hardware]
   health/failover driver: commander-pool-failover loop on Bes             [B, this dir]
   deploy gate: dc_deploy_canary.sh before any DC/supergateway change      [D, this dir]
```

## A — Worker pool auto-failover (follow-up, mcp-oauth-worker lane)

The pool code routes to one configured origin today. Change: per-request failover —
try the active origin; on connect error / 5xx / timeout, retry the next healthy origin
in pool order within the same request, and record the failure in KV so subsequent
requests skip the dead origin until the health loop (B) marks it risen. No human in
the loop. Acceptance: kill Lumen's DC mid-session with Bes's origin configured; the
next `tools/call` succeeds via Bes with no manual action.

## B — Health-check / failover loop (shipped here)

`pool_failover.py` + `commander-pool-failover.service`. Runs on **Bes**, not Lumen —
the driver must survive the death of the node it fails away from. Each interval it
probes every backend with the real liveness check (`POST /mcp` JSON-RPC `tools/list`,
`Accept: application/json, text/event-stream` — the wq#2606 watchdog's proven probe,
not a TCP ping) plus the Worker's `/pool/health`. Hysteresis: `fail_threshold`
consecutive failures to demote, `rise_threshold` consecutive successes to promote —
one 70s blip (wq#3264 Mode B) must not flap the pool. On demote/promote it runs
configurable hook commands (today: page via ntfy + aq so the on-call path is exercised;
once A lands: the Worker admin call that edits the KV pool membership) and persists
state to a JSON file so restarts don't re-fire transitions. Stdlib-only, `--once` mode
for timer-driven operation, tested in `test_pool_failover.py` against local HTTP
stand-ins (healthy, dead, and flapping backends).

## C — ≥2 always-on backends on separate hardware (follow-up + physical)

Now: finish Bes (wq#2608 staged artifacts are in `ops/wq2608-dc-endpoint-bes-a1/`;
apply + verify `mcp-bes.lumennode.uk` answers `tools/list`). Then: P1 Pro purchase is
a **physical task for Frank** (buy 2 now, not 6 — two dedicated always-on backends
plus Lumen and Bes gives N+2; revisit after a month of uptime data). Each P1 Pro gets
the identical §3.2 recipe (`dc-gateway.service` + ingress + Access), then joins the
pool config. Frank pre-authorized the spend in the ticket.

## D — Deploy-time canary (shipped here)

`dc_deploy_canary.sh` + `mcp_probe.py`. Every DC crash-loop so far (wq#3294 root
cause 2, 6d25744a) was a bad upgrade meeting live traffic. The canary starts the
**candidate** DC/supergateway versions on a throwaway port in a scratch prefix,
runs a full MCP `initialize` + `tools/list` e2e against it with `mcp_probe.py`, and
exits nonzero on any failure. Wire it as a hard gate in front of every
`npm install -g` / unit restart on a pool backend: the upgrade script may only touch
the live service if the canary exits 0. A crashing DC build dies on a scratch port,
not on the control plane.

## E — OAuth state in durable KV (follow-up, mcp-oauth-worker lane)

Root cause 1: registration/token state held in Worker isolate memory dies on every
recycle (~70s idle), forcing re-auth and stranding sessions. Move client
registrations, auth codes, and refresh-token grants to Workers KV (or one DO if we
need transactional writes); isolate memory may only be a read-through cache.
Acceptance probe must be able to fail: register, force a recycle (deploy a no-op or
wait out idle), then exercise the token — it must still work.

## Failure matrix (target state)

| Failure | Today | After |
|---|---|---|
| CF Worker isolate recycle | OAuth state lost, re-auth | state in KV, invisible [E] |
| DC upgrade crash-loops | control plane down for hours | canary blocks bad build pre-live [D] |
| Lumen DC dies | down until human intervenes | Worker retries next origin in-request [A]; loop demotes + pages [B] |
| Bes dies too | n/a (no 2nd backend live) | P1 Pro origins [C] |
| Driver node dies | n/a | loop runs on Bes + P1 Pro, idempotent via state file [B] |

## Follow-up tickets filed by this lane

1. [A+E] Worker: per-request origin failover + OAuth state to KV (mcp-oauth-worker
   repo + wrangler deploy lane; E is the half that kills the most outages).
2. [C] Verify/apply Bes dc-gateway live, add it to the pool config (needs ssh bes).
3. [C, physical, Frank] Buy 2 Station P1 Pros; on arrival a build lane runs the §3.2
   recipe per node.
4. [B+D deploy] Install `commander-pool-failover` on Bes and wire
   `dc_deploy_canary.sh` into the DC upgrade path on every pool backend
   (needs ssh bes + root on backends).
