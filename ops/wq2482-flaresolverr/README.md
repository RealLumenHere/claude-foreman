# wq#2482 — FlareSolverr as a loopback-bound fleet service

T2 from `research/DIGEST_Fleet_searchability_md.md` (wq#2464 digest of
Frank's fleet-searchability doc). Companion to wq#2481 (T1, the tiered fetch
ladder): that ticket's rung 4 needs this service but is written to degrade
to `ok:false` if it's absent, so the two land in either order.

## Why not the doc's K3s manifest

Frank's source doc (`FS L110-164`) ships a Kubernetes Deployment. Skipped
for two independent reasons, both verified live rather than assumed:

1. No cluster on this fleet — `which k3s kubectl helm` -> rc=1.
2. The manifest is malformed anyway: it uses `environment:` where the Pod
   spec key is `env:` (`FS L138`), so it wouldn't apply even with a cluster.

Instead this runs the upstream container under the fleet's existing
docker + systemd convention — same shape as
`ops/wq1299-phoenix-tracer/phoenix.service`: `ExecStartPre=docker rm -f`,
`ExecStart=docker run --rm --name ...`, `ExecStop=docker stop`,
`Restart=on-failure`, systemd `MemoryMax=`/`MemoryHigh=` instead of docker
`--memory` flags.

## Components

| File | Deploy target | Purpose |
|---|---|---|
| `flaresolverr.service` | `/etc/systemd/system/flaresolverr.service` | runs `ghcr.io/flaresolverr/flaresolverr:latest`, `-p 127.0.0.1:8191:8191` only, `MemoryMax=1G`/`MemoryHigh=512M` |
| `install.sh` | — | idempotent installer: installs the unit, redeploys the updated `ops/wq1184-gatus-health-checks/config.yaml` to `/home/lumen/kb/gatus/config.yaml`, restarts `lumen-gatus.service` |
| (edit, not new) `../wq1184-gatus-health-checks/config.yaml` | `/home/lumen/kb/gatus/config.yaml` | adds the `flaresolverr-liveness` endpoint to the existing fleet-hardening Gatus instance |

No new Gatus instance and no new port for Gatus itself: the fleet already
runs two — `sentinel-gatus` (docker, `:8418`, push-style heartbeat probes)
and the wq#1184 native instance (`:8783`, active HTTP polling of loopback
fleet services, e.g. `kb-mcp`, `despag-uhp`, `ollama-isis-tags`). A plain
`GET /` liveness check is exactly what the wq#1184 instance already does
for comparable services, so the new endpoint goes there rather than forcing
a push-heartbeat shape (token, external pusher) onto a service that's
natively pollable.

## Why FlareSolverr must never be cloudflared-exposed (instinct 6)

`POST /v1` accepts an arbitrary target URL and fetches it through a real
browser with no authentication (upstream, by design — it's a solver, not a
gateway). Exposing it beyond loopback turns it into an open SSRF/fetch
proxy for anything that can reach the fleet's cloudflared tunnel or
tailnet. `-p 127.0.0.1:8191:8191` in the unit makes off-host access
physically impossible at the docker port-publish layer, not merely
firewalled — there's no config flag downstream that can widen it by
accident.

## Why the container isn't started and the Gatus config isn't live-deployed
by this BUILD worker

Same constraint as `ops/wq1299-phoenix-tracer/`: this sandbox's guard hook
denies `docker` beyond `ps`/`logs`, and `/etc` plus the live
`/home/lumen/kb/gatus/config.yaml` deploy path are outside this worktree's
write scope for a systemd/container install. This PR ships the unit,
the Gatus config edit (git-tracked, same file wq#1184's own `install.sh`
copies from), and an idempotent `install.sh`; a root/interactive session
runs it on lumen and records the result.

## Test / verify procedure (for whoever runs `install.sh`)

1. `ops/wq2482-flaresolverr/install.sh` — brings up `flaresolverr.service`,
   redeploys Gatus config, restarts `lumen-gatus.service`.
2. `curl -sf http://127.0.0.1:8191/` from lumen itself — expect HTTP 200,
   body containing `"FlareSolverr is ready!"`.
3. From a different fleet node (not lumen), confirm `100.117.66.102:8191`
   is unreachable (connection refused/timeout) — proves the loopback bind,
   not just a firewall rule, is what's keeping it off-tailnet.
4. `curl -s http://127.0.0.1:8783/api/v1/endpoints/statuses | grep flaresolverr`
   (or the Gatus UI at `127.0.0.1:8783`) — confirm `flaresolverr-liveness`
   reads healthy.
5. One real challenge-solve, matching the ticket's acceptance line:
   `POST http://127.0.0.1:8191/v1` with
   `{"cmd":"request.get","url":"<a known Cloudflare-challenged URL>","maxTimeout":60000}`
   — expect `"status":"ok"` in the response. Deliberately not automated as
   a recurring Gatus check (spins up headless Chrome and hits a real remote
   site every poll); it's a one-time deploy verification instead.
6. `sudo reboot` lumen (outside the Thor hard-rule — lumen reboots are
   fine with ordinary care) and re-check step 2 — confirms
   `WantedBy=multi-user.target` survives a reboot as the ticket requires.
