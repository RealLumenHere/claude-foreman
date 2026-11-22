# wq#2606 — mcp-gateway-watchdog: liveness probe (tools/list), not PSI

## Problem

`mcp-pressure-guard.sh` (`/home/lumen/bin/mcp-pressure-guard.sh`) only trips on
two resource proxies: `/proc/pressure/memory` PSI and the gateway cgroup's
`MemoryCurrent`. Neither one fires when supergateway is hung with an open TCP
port — e.g. `kill -STOP` on the child process, an event-loop wedge, a deadlock
on a stuck child reap. The port still accepts connections, PSI stays flat,
cgroup memory doesn't grow, and the guard never trips while every MCP call
against it hangs until the client's own timeout. Liveness (does it actually
answer a request) and health (is the box under resource pressure) are
different signals; this ticket adds the liveness one, per
`MCP_CONTROL_PLANE_HA_DESIGN.md` §3.3.

## What this adds

- `mcp-gateway-watchdog.sh` — node-agnostic probe + restart script. Takes
  `--mode {mcp|cloudflared} --unit <systemd-unit> --port <port>` so the same
  binary ships unmodified to Anubis/Bes/Nephthys, where the gateway unit is
  `dc-gateway` instead of `desktop-commander-mcp` (§3.2). Two probes:
  - `mcp`: `POST /mcp` JSON-RPC `tools/list` with
    `Accept: application/json, text/event-stream`, `curl -m 10`. Success =
    HTTP 200 with a `"result"` key in the body (handles both a plain-JSON and
    an SSE-framed streamable-HTTP response — both put `"result"` in the body
    text).
  - `cloudflared`: `GET /ready` on the tunnel's `--metrics` port.
  Both share one restart/cooldown/flap state machine (below); only the probe
  function differs, so the class of bug (liveness != health) is fixed once.
- `mcp-gateway-watchdog.service` / `.timer` — runs both probes every minute
  (`OnUnitActiveSec=60`), matching `mcp-pressure-guard.timer`'s cadence.
- `cloudflared-metrics.conf` — drop-in adding `--metrics localhost:20241` to
  `cloudflared.service` (today's unit has no metrics/health endpoint at all —
  verified via `systemctl cat cloudflared`), which is what makes the
  `/ready` probe possible.
- `supergateway-logrotate.conf` — `/home/lumen/logs/supergateway/*.log`,
  `size 50M rotate 3`. Broadens wq#1698's single-file
  `supergateway-desktop-commander` logrotate proposal (still unapplied — the
  log is 64MB today, confirmed by the ticket, because that proposal never
  got installed to `/etc/logrotate.d`) to a glob so any additional local
  supergateway instance is covered too. Also a wq#2546 secrets-at-rest
  mitigation: the log holds unmasked tool-call bodies (wq#1698's finding),
  so bounding its size bounds how much of that sits on disk.
- `install.sh` — root-run installer (this worker's sandbox cannot write
  `/etc/systemd/system/*` or run `daemon-reload`/`systemctl restart` as root,
  same constraint as wq#1698, wq#1990, wq#1501 before it): installs the
  script to `/home/lumen/bin/`, the timer+units to `/etc/systemd/system/`,
  the logrotate config to `/etc/logrotate.d/`, enables the timer, and
  restarts `cloudflared` once (detached via `systemd-run`, quiet-window
  guarded, health-checked) to pick up `--metrics`. Does **not** restart the
  gateway unit itself — no reason to bounce it during rollout, the watchdog's
  own probe is what restarts it from here on. Added by wq#2637's verify pass:
  `install.sh --verify` (ops/VERIFY_CONTRACT.md, wq#1696) — read-only, checks
  the watchdog script, both units, the cloudflared drop-in and the logrotate
  config against what's actually live, and (only when `WATCHDOG_ON=1`)
  enabled+active+scheduled on the timer. wq#5264: the timer is intended-off
  (2026-10-08 subtraction, `scratch/subtraction/timers-disable-list.txt:79`),
  so `WATCHDOG_ON=0` ships: install leaves the timer alone and --verify does
  not assert its state. Re-enabling is a decision: flip `WATCHDOG_ON=1`. Runs
  clean today for the `/home/lumen/bin` copy but reports `VERIFY FAIL (6)`
  for everything else, since none of the root-owned artifacts are installed
  yet (see "What this does NOT cover" below).
- `/home/lumen/bin/mcp-gateway-watchdog.sh` — this worker's sandbox *can*
  write to `/home/lumen/bin` (non-privileged, unlike `/etc`), so the script
  is already deployed there live, executable, ready for `install.sh` to wire
  the timer to it. It is byte-identical to the copy in this directory
  (`install.sh` reinstalls the same source, so a future run is idempotent).

`mcp-pressure-guard.sh` and its timer are untouched — this is an addition on
the responsiveness axis, not a replacement.

## Restart / flap logic (in `mcp-gateway-watchdog.sh`)

Per `(mode, unit)` pair, state lives in `/run/mcp-gateway-watchdog/<unit>.*`
(tmpfs, root-owned via the timer, reset on reboot — matches
`mcp-pressure-guard.sh`'s `/run/mcp-pressure-guard.last` convention):

1. Probe fails -> consecutive-fail counter increments. `< 2` -> exit, no action.
2. `>= 2` consecutive fails -> restart is considered:
   - if 2 restarts already happened in the last 1800s (30 min) window ->
     **flapping**: refuse to restart, `ntfy-send` + `aq add` an alert (once
     per flap episode, not every tick), log, reset the fail counter.
   - else if the last restart was `< 300s` ago -> cooldown suppresses this
     restart (log only), fail counter left as-is so the next tick re-attempts
     without needing 2 fresh fails.
   - else -> restart the unit, record the timestamp, reset the fail counter.
3. A successful probe at any point resets the fail counter to 0.

All thresholds (`WATCHDOG_COOLDOWN=300`, `WATCHDOG_RESTART_WINDOW=1800`,
`WATCHDOG_MAX_RESTARTS=2`, `WATCHDOG_FAIL_THRESHOLD=2`), the state dir, the
restart command, and the alert sinks are env-overridable — this is what let
the test below run against a mock HTTP server and a fake restart command
with compressed timers, with zero footprint on the real fleet.

## Test-to-failure

The ticket's live version (`kill -STOP` the real supergateway pid, wait up to
2 min for a restart; force 3 restarts, confirm the flap alert fires and
auto-restart stops) needs root to install the timer and to run
`systemctl restart` on the real unit — both denied to this worker's sandbox
(ISSUE-LOG #68 class: no privilege-escalation verb). What's demonstrated
instead, without touching the real fleet, is the same state machine end to
end against a mock server:

`/tmp/fworker-wq2606/test/run_test.py` starts a local HTTP server standing in
for the gateway (`/mcp` returns a valid `tools/list` result when "healthy",
HTTP 599 when not) and a fake `WATCHDOG_RESTART_CMD` that just appends to a
log instead of calling `systemctl`, then drives
`mcp-gateway-watchdog.sh --mode mcp ...` through:

1. healthy probe -> 0 restarts.
2. 2 consecutive failures -> exactly 1 restart (not 1 failure, not 3).
3. still failing, still inside the 2s test cooldown -> restart suppressed.
4. cooldown cleared, still failing -> 2nd restart.
5. 2 restarts already in the window -> 3rd attempt refused, flap-alert stamp
   written exactly once.
6. probe recovers -> fail counter resets to 0.

A second run confirms `--mode cloudflared` (`GET /ready`) resets its fail
counter to 0 against a healthy mock. Output: `ALL CHECKS PASSED (2 restarts
recorded, flap alert fired, cooldown honored, recovery reset fails)`.

Once `install.sh` runs on a real node (Frank/an authorized deploy path — same
carve-out as wq#1698), the live kill-`-STOP` drill in the ticket becomes
runnable and should be executed there before this is treated as fully
verified in production; that's out of this worker's reach, not skipped.

## What this does NOT cover

- Actually running `install.sh` on Lumen or any endpoint node — needs root,
  outside this worker's sandbox (same carve-out as wq#1698/wq#1990/wq#1501).
  Filed as the physical/authorized-deploy follow-up, not a blocker on this
  ticket (full-autonomy instinct's Frank carve-out).
- Rolling this out to Anubis/Bes/Nephthys — depends on §3.2 (node bring-up)
  landing first; `install.sh` already takes `WQ2606_GATEWAY_UNIT=dc-gateway`
  for when it does.
