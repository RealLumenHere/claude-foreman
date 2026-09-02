# wq#1184 — Gatus health checks (bridge-safe, config-as-code) + cron dead-man

TIER 1 fleet hardening from `kb/fleet-upgrade-plan.md` + the Fable review
(`kb/tooling-findings-review.md`).

## What this is

A **second**, native Gatus instance — single Go binary, systemd unit, SQLite
storage, no docker — separate from the existing `sentinel-gatus` docker
container (wq#856, config at `/home/lumen/sentinel/config/config.yaml`,
which hosts push-style "5-probes" endpoints; see `ops/wq667-gatus-resolve-workaround/`
for its known alert-resolve quirk). This instance does its own **active
HTTP polling** of five core fleet services, plus a Healthchecks.io-style
dead-man's switch for `lumen-*` cron/timer stamp files.

Split rationale: the existing instance is a passive receiver for
externally-pushed heartbeats; this ticket's ask (active probing, hard
bridge-safety rules, config-as-code in kb) is a different operating mode and
a different config file, so a second lightweight instance is simpler and
safer than overloading the docker one's config with unrelated semantics.

## Components

| File | Deploy target | Purpose |
|---|---|---|
| `config.yaml` | `/home/lumen/kb/gatus/config.yaml` | Gatus config — 5 endpoints, HARD BRIDGE RULES in the header |
| `gatus.service` | `/etc/systemd/system/lumen-gatus.service` | runs the Gatus binary |
| `ntfy_relay.py` | `/home/lumen/bin/gatus-ntfy-relay` | loopback shim: Gatus's `custom` webhook alert -> `/home/lumen/bin/ntfy-send` |
| `gatus-ntfy-relay.service` | `/etc/systemd/system/lumen-gatus-ntfy-relay.service` | runs the relay |
| `cron_deadman.py` | `/home/lumen/foreman/ops/wq1184-gatus-health-checks/cron_deadman.py` (run in place) | dead-man sweep over `/home/lumen/logs/.stamp.*` |
| `lumen-cron-deadman.service` + `.timer` | `/etc/systemd/system/` | runs the dead-man every 10 min |
| `cron_stamp.sh` | `/home/lumen/bin/lumen-cron-stamp` | `ExecStartPost` helper any `lumen-*.service` can adopt to touch its own stamp file |
| `install.sh` | — | idempotent installer, run via `lumen-run --root` |

## Why Gatus's own probes, not the docker instance's `ntfy` provider

Gatus ships a built-in `ntfy` alert provider that talks to an ntfy server
directly. The ticket requires alerting through the house `ntfy-send`
wrapper (priority routing + primary/OOB fan-out, wq#901), so this instance
uses Gatus's `custom` webhook provider pointed at a tiny loopback-only relay
(`ntfy_relay.py`) that shells out to `ntfy-send`. Alert-on-transition is
Gatus's own `failure-threshold`/`success-threshold` behavior — no extra
logic needed for that part.

## Why the dead-man only covers 3 units so far

No `lumen-*` unit on the host was touching a stamp file before this ticket —
confirmed by sampling ~10 unit definitions via `systemctl cat`, none
referenced `/home/lumen/logs/.stamp.*`. Retrofitting all ~68 live
`lumen-*` timers in one ticket is out of scope and unnecessarily invasive;
instead this ships the wrapper (`cron_stamp.sh`) plus wires it into the 3
units this repo already owns (`lumen-foreman-dispatch`, `-selfheal`,
`-health`), and `cron_deadman.py` only ever checks stamps that exist — so
coverage grows as other units adopt `ExecStartPost=-/home/lumen/bin/lumen-cron-stamp %n`
one at a time, without a blocking migration.

## Interval derivation for the dead-man

Rather than parsing `OnCalendar`/`OnUnitActiveSec` syntax, `cron_deadman.py`
reads each timer's actual next-elapse and last-trigger timestamps from
`systemctl list-timers --all -o json` and uses `next - last` as the
measured interval — this works identically for calendar- and
monotonic-scheduled timers.

See `RUNBOOK.md` for deployment, verification, and rollback.
