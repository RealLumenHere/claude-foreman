# Staged install — lumen-foreman-dispatch (wq#715) + lumen-foreman-selfheal (wq#725)

The build ships these units **repo-only** (writing /etc is outside worker scope).
Nothing below is run by the fleet; DESIGN.md §10 (dispatcher) and self_heal.py's module
docstring (wq#725) are the contract.

## 1. Install, WITHOUT enabling (staged)

```bash
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-dispatch.service /etc/systemd/system/
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-dispatch.timer   /etc/systemd/system/
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-dispatch.logrotate /etc/logrotate.d/lumen-foreman-dispatch
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-selfheal.service /etc/systemd/system/
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-selfheal.timer   /etc/systemd/system/
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-selfheal.logrotate /etc/logrotate.d/lumen-foreman-selfheal
sudo systemctl daemon-reload
systemctl is-enabled lumen-foreman-dispatch.timer   # expect: disabled
systemctl is-enabled lumen-foreman-selfheal.timer   # expect: disabled
```

## 2. Prove it inert (optional, recommended)

```bash
python3 /home/lumen/foreman/dispatcher/foreman_dispatcher.py run --once --dry-run
tail -5 /home/lumen/foreman/dispatcher.log

python3 /home/lumen/foreman/dispatcher/self_heal.py run --once --dry-run
tail -5 /home/lumen/foreman/self_heal.log
```

All lanes ship `enabled: false` in dispatcher/config.json, so even an enabled dispatch
timer dispatches nothing until a lane is switched on by config PR (plus a LANE_REPO
entry in orchestrator/router.py — DESIGN.md §12.1: Frank confirms each repo path).
self-heal has no lane gate of its own — it only ever acts on tickets already blocked
with an `INFRA_CRASH:` reason (harness/fworker.py's classify_block_reason()), so it is
safe to enable independently of which lanes the dispatcher has turned on.

## 3. Enable (Tier-3, Frank only)

```bash
sudo systemctl enable --now lumen-foreman-dispatch.timer
sudo systemctl enable --now lumen-foreman-selfheal.timer
```

## Rollback

```bash
touch /home/lumen/autonomy/DISPATCH_DISARM        # instant, no systemd needed — both
                                                   # the dispatcher and self-heal check it
# or: sudo systemctl disable --now lumen-foreman-dispatch.timer
# or: sudo systemctl disable --now lumen-foreman-selfheal.timer
```

## wq#718 AUTO-6 — circuit breaker, runaway detector, dashboard

The circuit breaker (`circuit_breaker.py`) and runaway detector (`runaway.py`) need
**no separate install step**: they're called in-process from every
`foreman_dispatcher.py` pass (see its module docstring's stage 0.5 / stage 3.5), so
they go live automatically the moment `lumen-foreman-dispatch.timer` is enabled per
step 3 above. Nothing here changes that timer's staged/disabled default.

Sanity-check the breaker and detector independently of a real pass:

```bash
python3 /home/lumen/foreman/dispatcher/circuit_breaker.py status
python3 /home/lumen/foreman/dispatcher/runaway.py list-stuck
python3 /home/lumen/foreman/dispatcher/circuit_breaker.py test-ntfy   # sends one labeled
                                                                       # test ntfy; never
                                                                       # touches DISARM
```

Defaults (env-overridable — see circuit_breaker.py's constants):
`FOREMAN_BREAKER_WINDOW_MIN=90`, `FOREMAN_BREAKER_MIN_SAMPLES=8`,
`FOREMAN_BREAKER_THRESHOLD=0.75` (fraction of the window's attempts that must classify
LAUNCH_FAILURE/KILLED/OOM — harness/fworker.py's INFRA_CRASH bucket — before the
breaker trips), `FOREMAN_RUNAWAY_MAX_ATTEMPTS=5`. These were calibrated against this
fleet's actual attempt history at build time (see circuit_breaker.py's module
docstring) — re-check them if the fleet's ambient failure rate shifts a lot.

## wq#825 — dispatcher-not-dispatching alarm

`dispatch_health.py` (not `dispatcher_health.py` — that duplicate was retired in
wq#1416, see `self_heal.py`'s docstring) is the sole DISPATCHER-STALLED pager. It
writes `health_state.json` and runs the config-lint on `lumen-foreman-health.timer`
(5-min cadence), independent of `self_heal.py`.

Sanity-check it independently of a real pass:

```bash
python3 /home/lumen/foreman/dispatcher/dispatch_health.py status
python3 /home/lumen/foreman/dispatcher/dispatch_health.py check --dry-run
```

Defaults (env-overridable — see dispatch_health.py's constants):
`FOREMAN_HEALTH_CONSECUTIVE=2` (consecutive unhealthy dispatcher passes — an
`event=error` line, or a `pass_end` with `spawned=0` while `candidates>0` — before
paging Frank), `FOREMAN_HEALTH_ALERT_DEDUP_SECONDS=3600` (re-ping cadence while the
dispatcher stays unhealthy, so a still-broken dispatcher isn't a single missable ntfy
over a long outage but also doesn't spam every 5-min health-timer cycle).

### Dashboard (`dashboard.py`)

Install, staged, same posture as the dispatch/self-heal units:

```bash
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
systemctl is-enabled lumen-foreman-dashboard.service   # expect: disabled
```

Prove it inert / working locally before exposing it anywhere:

```bash
python3 /home/lumen/foreman/dispatcher/dashboard.py --bind 127.0.0.1 --port 8791 &
curl -s http://127.0.0.1:8791/api/snapshot | head -c 400
kill %1
```

Enable (Tier-3, Frank only) — binds loopback-only, not internet-reachable by itself:

```bash
sudo systemctl enable --now lumen-foreman-dashboard.service
```

**Making it reachable at `dispatch.lumennode.uk` behind Cloudflare Access is DNS +
Cloudflare-account work this worker has no credentials or network access to perform.**
The acceptance criterion ("dashboard live at chosen URL, behind Cloudflare Access")
needs Frank to do, roughly:

1. Point a DNS record (e.g. `dispatch.lumennode.uk`) at this box (or its existing
   Cloudflare Tunnel, if one's already in use for other lumennode.uk subdomains).
2. Reverse-proxy the public hostname to `127.0.0.1:8791` — either a Cloudflare Tunnel
   ingress rule straight to that port, or a local Caddy/nginx vhost that the tunnel (or
   a direct DNS A/AAAA record) points to. No proxy config is included here because it
   depends on whether this box already runs a tunnel/proxy for other subdomains —
   copying a real one blind risks breaking it.
3. Put a Cloudflare Access policy in front of that hostname (Zero Trust → Access →
   Applications) restricted to Frank's identity — the guardrail is explicit that this
   dashboard must stay read-only *and* access-gated even though its own HTTP layer
   never exposes a mutating verb (see dashboard.py's module docstring).

Rollback is the same kill switch as the dispatcher: `touch DISPATCH_DISARM` stops new
spawns; the dashboard itself only reads, so `sudo systemctl stop
lumen-foreman-dashboard.service` (or leaving it disabled) is the only "off" it needs.

### Window gate + Thursday tightened watch (wq#1411)

Three timers, two services. `lumen-foreman-gate@.service` is a template: `window`
(Thu 00:30 PT, before the Max reset ~04:00 PT; adds the 6h `resets_at` check) and
`nightly` (22:00 PT, same gate without it). `lumen-foreman-health-window.*` runs the
existing classifier at K=1 / 20-min dedup, Thu 01:00-08:55 PT every 5 min, sharing
`runtime/health_state.json` with the regular instance and never touching the
recovery ladder (`--no-ladder`; the regular K=2 timer is its only actor).

```bash
cd /home/lumen/foreman/dispatcher/systemd
sudo cp lumen-foreman-gate@.service lumen-foreman-window-gate.timer lumen-foreman-nightly-gate.timer \
        lumen-foreman-health-window.service lumen-foreman-health-window.timer /etc/systemd/system/
sudo systemctl daemon-reload
systemd-analyze calendar 'Thu 00:30 America/Los_Angeles' '*-*-* 22:00 America/Los_Angeles' \
                         'Thu *-*-* 01..08:00/5 America/Los_Angeles'   # TZ suffix must normalize
```

Prove it before enabling — read-only, no ntfy, exit 2 on any failing check:

```bash
python3 /home/lumen/foreman/dispatcher/dispatch_recover.py gate --mode nightly --dry-run
python3 /home/lumen/foreman/dispatcher/dispatch_health.py check --threshold 1 --dedup 1200 --no-ladder --dry-run
```

A real run (`gate --mode nightly`) sends exactly one ntfy to lumen-alerts: default
`[DISPATCH] nightly gate OK`, or urgent `[DISPATCH] nightly gate FAILED` listing every
failing check. It reproves stale stamps in place and, if the dry-run pass would not
spawn, runs ladder rungs 2-3 (restart timer+service, JSON diagnose) and rechecks.
Report: `dispatcher/runtime/gate_last.json`.

Enable:

```bash
sudo systemctl enable --now lumen-foreman-window-gate.timer lumen-foreman-nightly-gate.timer lumen-foreman-health-window.timer
systemctl list-timers --no-pager | grep -E 'gate|health-window'
```

Rollback: `sudo systemctl disable --now` any of the three timers. `FOREMAN_RECOVER_DISABLE=1`
in a drop-in makes the gate report-only (no reproves, no restarts). `DISPATCH_DISARM`
is read by the gate, never written.

### Budget governor poll daemon — `lumen-foreman-usaged` (wq#613, wq#1458)

Long-running poll loop (`governor/foreman_usaged.py run`), not a one-shot pass —
`Type=simple` + `Restart=always`, no timer. This replaces the old `lumen-run
start --name foreman-usaged ...` posture documented previously in
`governor/README.md`'s Running section: a `lumen-run` unit does not survive a
reboot or a DC-gateway process-group reap, which is exactly how the daemon went
dead-and-unnoticed for 15 days (wq#1458 — last poll 2026-08-23, no process, no
unit, `state.json` frozen stale).

```bash
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-usaged.service /etc/systemd/system/
sudo systemctl daemon-reload
systemctl is-enabled lumen-foreman-usaged.service   # expect: disabled
```

Prove it before enabling — a single poll, no daemon loop:

```bash
python3 /home/lumen/foreman/governor/foreman_usaged.py run --once
python3 /home/lumen/foreman/governor/foreman_usaged.py status
```

Enable (Tier-3, Frank only):

```bash
sudo systemctl enable --now lumen-foreman-usaged.service
systemctl status lumen-foreman-usaged.service --no-pager
```

Rollback: `sudo systemctl disable --now lumen-foreman-usaged.service`. Every
`can-dispatch` call also fail-closes on its own once `state.json`'s
`updated_at` outlives `floors.json`'s `stale_fail_closed_seconds` (default
600s) — see `foreman_usaged.py`'s `_daemon_stale()` — so even an un-enabled
or crashed unit degrades to local-rung-only rather than allowing dispatch on
stale data.

### observed_burn.json sampler — `lumen-foreman-burnrate` (wq#1722)

15-min timer, one-shot poll (`observed_burn_writer.py run --once`), same staged
posture as dispatch/selfheal/health. Samples governor `state.json` percent per pool,
counts cloud fworker worker-hours since the last poll (`lumen-run list` for units
still running + `journalctl` for units that already exited, ticket->model resolved
via `concurrency.ticket_model` so local-rung units are excluded and fable units are
split into `fable_weekly_scoped`), skips windows that span a reset, and writes a
per-pool EMA to `dispatcher/observed_burn.json` — which `burn_rate.load_observed()`
already reads and prefers over its one-night 2026-09-10 defaults (wq#1699
"Measurement method"). No dispatcher coupling beyond that shared file.

```bash
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-burnrate.service /etc/systemd/system/
sudo cp /home/lumen/foreman/dispatcher/systemd/lumen-foreman-burnrate.timer   /etc/systemd/system/
sudo systemctl daemon-reload
systemctl is-enabled lumen-foreman-burnrate.timer   # expect: disabled
```

Prove it before enabling — a single poll, no daemon loop:

```bash
python3 /home/lumen/foreman/dispatcher/observed_burn_writer.py run --once
python3 /home/lumen/foreman/dispatcher/observed_burn_writer.py status
```

Enable (Tier-3, Frank only):

```bash
sudo systemctl enable --now lumen-foreman-burnrate.timer
systemctl list-timers --no-pager | grep burnrate
```

Rollback: `sudo systemctl disable --now lumen-foreman-burnrate.timer`. Deleting
`dispatcher/observed_burn.json` (or leaving the timer disabled so it's never
written) falls back to `burn_rate.py`'s measured-2026-09-10 config defaults —
`load_observed()` treats an absent/unreadable file as "nothing measured yet", never
as a zero rate.
