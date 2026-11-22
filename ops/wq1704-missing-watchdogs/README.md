# wq#1704 — the watchdogs FLEET_SERVICE_ATLAS flagged as missing

`research/FLEET_SERVICE_ATLAS.md` §5 ranked the fleet's single points of failure
and §5.1 listed the seven watchdogs that would close them. Three of the seven
were already taken by sibling tickets and have landed or are staged:

| §5.1 item | closes | owner | state |
|---|---|---|---|
| 1. universal timer dead-man (auto cron-stamp drop-in) | §5 #3, #4 | wq#1658 | `ops/wq1658-cron-stamp-dropin/` |
| 4. guard-naming-a-ghost check in retirement-audit | §5 #6 | wq#1644 | landed (`9501676`) |
| 5. failed-units "finding-exit" allowlist | §5 #1 | wq#1657 | `ops/wq1657-guard-exit-convention/` |

This ticket takes the four that were still open. One check, one service, one
timer, one `OnFailure=` each.

| check | unit | closes | the silent failure it ends |
|---|---|---|---|
| `daemons` | `lumen-spof-daemons.timer` (5 min) | §5 #5, §5.1 item 7 | `lumen-gatus-ntfy-relay` (:8784), `ollama` (:11434), `lumen-guardian`, and the two ntfy subscribers had no external check at all. `Restart=` is not a watchdog: a wedged daemon stays `ActiveState=active` forever, so the port probe is the point. |
| `roundtrip` | `lumen-spof-roundtrip.timer` (15 min) | §5 #5, §5.1 item 2 | Proves **publish → subscribe → persist**, not just publish. Publishes a marker to `lumen-updates` and asserts an `alerts.db` row carrying it appears within 120 s. |
| `restarts` | `lumen-spof-restarts.timer` (30 min) | §5 #7, §5.1 item 3 | 64 daemons on this box carry `Restart=always`/`on-failure` with no `StartLimitBurst` ceiling. A permanently dead dependency becomes an infinite quiet loop; this alerts on an `NRestarts` delta ≥ 5 per 24 h window. |
| `timers` | `lumen-spof-timers.timer` (10 min) | §5 #4, §4.12, §4.13 | `wadjet-ha-monitor.timer` (Frank's password manager) and the backup chain: atlas §4.12's "notices: **nobody**". Asserts both *fired recently* and *the run succeeded*. |

## Why the round-trip is not just "the deadman beat already does it"

Atlas §5 #5 suggests reusing the deadman beat's heartbeat. It cannot be reused:
`alert-deadman-beat.sh:11` publishes to its own `lumen-heartbeat` topic
precisely so the beat stays invisible, and `lumen-alert-queue.service`
subscribes to `lumen-alerts,lumen-updates` only. The beat therefore proves ntfy
*accepts* a publish and nothing about the subscriber. This check publishes onto
a topic the subscriber actually watches, at `min` priority (silent on the phone,
and never gated — `ntfy-send:23`), and resolves its own probe row afterwards so
synthetic traffic cannot feed the §5 #2 triage pile.

## Two assertions, not one

`timers` checks the timer's `LastTriggerUSec` **and** the service's `Result`
separately. ISSUE-LOG #44 is the reason: wq#1252 conflated "ran recently" with
"the data is fresh" via a file mtime and went red on a healthy system. A timer
that fires into a service that fails every time is the inverse trap — a
trigger-recency check alone calls it healthy.

`max_age_s` per timer is generous-2× of the cadence read off `systemctl
list-timers` on 2026-09-11, not derived at runtime: a 30 s timer cannot be
bounded at 2× by a guard that itself runs every 10 minutes.

## Exit-code contract

These guards always exit 0, reporting via `ntfy-send` + `aq.ingest`, which is
the branch wq#1657 picked as preferred (the load-watch template). A red
`lumen-spof-*` unit therefore means the guard crashed, not that it found
something — and `ntfy-onfailure@%n.service` on each unit means what it says.
Findings edge-clear: when a target recovers, its open `aq` row is resolved.

Each unit also carries `ExecStartPost=-/home/lumen/bin/lumen-cron-stamp %n`, so
these watchers are themselves watched by `lumen-cron-deadman` (atlas §3.5, "who
watches the watchers"). wq#1658's generator would add this drop-in anyway; the
explicit line means coverage does not wait on that ticket.

## Verify

```
python3 -m pytest ops/wq1704-missing-watchdogs/tests -q   # 13 passed
python3 ops/wq1704-missing-watchdogs/smoke.py             # real systemd, throwaway db
python3 ops/wq1704-missing-watchdogs/verify_fires.py      # 4/4 fire on simulated failure
```

`smoke.py` runs the read-only checks against *real* `systemctl` output with a
throwaway `alerts.db` and a fake `ntfy-send`, so it can neither page Frank nor
add rows to the live queue — it proves the canned output the unit tests use is
the shape this box emits. `verify_fires.py` drives each check into its failure
branch and prints the alert it raises.

## Install

```
ssh lumen@100.117.66.102 'lumen-run --root bash /home/lumen/foreman/ops/wq1704-missing-watchdogs/install.sh'
```

`install.sh` gates on the suite and on `verify_fires.py` before it installs
anything, installs all four unit pairs, then enables and runs one real pass of
the two intended-on timers only (`ENABLED=(daemons timers)`).

**wq#5257: `roundtrip` and `restarts` are intended-off.** The 2026-10-08
subtraction disabled both on purpose
(`/home/lumen/scratch/subtraction/timers-disable-list.txt:71-72`) and kept
`daemons`/`timers` (`timers-keep-phase2.txt:28-29`). `--verify` used to demand
all four enabled+active, so it failed forever, and the ops-root-apply it filed
(wq#5257) said to run this script as root, which would have re-enabled both and
undone the subtraction. `--verify` now checks all four unit files plus
enabled/active/scheduled for the `ENABLED` set only, and the install loop only
enables that set. Re-enabling either timer is a decision: add it to `ENABLED`.

## Known gaps left open, and why

- **`lumen-command-receiver` round-trip.** Covered here by liveness only. A
  true round-trip means publishing a command to the control topic, and
  `ntfy_command_receiver.py` lives under `/home/lumen/agents/command-receiver/`,
  outside this lane's granted roots — its command vocabulary could not be read,
  and inventing a "ping" verb blind would be a guess. Needs a read grant or a
  receiver-side no-op verb; filed as the remainder of §5.1 item 2.
- **Healer-table entries (§5.1 item 7's literal form).**
  `/home/lumen/agent/healer_watch_table.yaml` is also outside the granted roots,
  so its schema could not be read and a merge fragment would have been guessed.
  The `daemons` check gives the same coverage independently — arguably better,
  since it does not depend on the healer's own health — but the healer still has
  no restart ladder for these five units. wq#1650 owns that half.
- **Backup snapshot-age push to Gatus (§5.1 item 6).** The recency question is
  answered here from systemd (`timers` asserts the run happened *and*
  succeeded). Pushing snapshot age into Gatus needs
  `/home/lumen/services/backup/scripts/backup-critical.sh` and the Gatus push
  URL, both outside the granted roots.
