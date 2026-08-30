# R3 — Interim-Fix Inventory + Retirements

Session: wq924-r3-0d2c7c (wq#924)  ·  Executed: 2026-08-27  ·  Host: Lumen only (Thor never touched)
Mandate: OPS_PROCESS_RESEARCH_R1-R5.md §R3 — find every interim artifact fleet-wide; for each,
is the permanent replacement DONE? If yes, retire it and prove nothing depended on it. If no,
confirm the real-fix ticket exists, is prioritised, and the interim is LABELLED in code.

## Method
rg over operational trees (bin, agent, agents, autonomy, foreman, mission-control, services,
instructions, prompts, glance, ha, sentinel, maint, ops, tools, dashboard, power-dash,
telemetry, uplift, hardening, infra, mcp-servers, minerva, etc) for:
`interim | stopgap | bandaid | until wq# | TEMP FIX | superseded by | temporary (fix|hack|workaround|shim)`.
Data/model/vendored trees (backups, models, node_modules, container-backups, llama.cpp) and
run-logs/boards/sessions excluded as noise. Live state verified per artifact (systemd unit
state, timers, cron, running processes, invocation grep) BEFORE any change.

## Disposition summary

| Interim artifact | Real fix | Real fix done? | Decision | Executed |
|---|---|---|---|---|
| `dispatch-deadman.service`/`.timer` (hand-installed units) | wq#825 (dispatcher_health in self_heal) | YES | RETIRE | ✅ units removed + verified |
| `bin/dispatch-deadman.sh` | wq#825 | YES | already retired | (was `.retired.wq825`) |
| `bin/night-watch.sh` | wq#825 (alarm) + dispatcher timer (trigger) | YES | RETIRE | ✅ → `.retired.wq825` |
| `bin/chain-runner.sh` | wq#715 (foreman dispatcher) | YES | RETIRE | ✅ → `.retired.wq715` |
| turn-ceilings @ 600 | per-archetype assistant-turn counting (fworker.py) | YES | no-op (label only in night-watch, now retired) | n/a |
| `worker-settings-interim.json` | `worker-settings.json` | YES | already deleted (lint test asserts) | n/a |
| `fleet-telemetry` (wq#744) | wq#753 node_exporter | PARTIAL — not for MC | KEEP + ticket | wq#1013 |
| dup dispatcher-health watchers | (design pending) | NO | KEEP + ticket | wq#1012 |
| `etc/plug-recover.conf` ARMED=0 | wq#390 (Frank hw-watchdog policy) | OPEN (Frank decision) | KEEP, correctly labelled | n/a |
| instinct rule "interim until wq#715" | wq#715 (done) | YES | flagged for re-scope (A7 §4.1 #38); proposal-only, Frank retirer | n/a |

## Executed retirements (with evidence)

### 1. dispatch-deadman units — completed wq#997 apply phase
State at start: `bin/dispatch-deadman.sh` already renamed `.retired.wq825`, but the
hand-installed `dispatch-deadman.service`/`.timer` (not in any repo, predate the
systemd-in-repo convention) were still on disk. A prior session had `disable`d the timer
(episode: "Completed wq#825 retirement: disabled orphaned dispatch-deadman.timer/.service")
but left the unit files — so the timer showed disabled+inactive while the files lingered.
wq#997 (fleet-fires, pri 1) held a complete, correct retirement plan but its claiming session
`lumen:1484895` is DEAD (pid gone; lease unexpired until 17:38Z but no process) — an orphaned
apply phase, not live work.

Precondition (wq#997 Step 0) verified PASS before touching anything:
`lumen-foreman-selfheal.timer` enabled+active; `dispatcher_health.py status` clean
(streak 0, fresh `most_recent_pass_id`). Coverage is doubly live (see finding wq#1012).

Actions: snapshotted both units to `foreman/plans/wq997-dispatch-deadman.unit.bak{,-service,-timer}`;
`rm` the two files at `/etc/systemd/system/`; `daemon-reload`. Verified after:
- `systemctl status dispatch-deadman.{service,timer}` → "could not be found"
- `systemctl list-timers --all | grep deadman` → empty
- files gone from `/etc/systemd/system/`
- coverage still live: selfheal + health timers enabled+active, `dispatcher_health status` clean.

Rollback (if ever needed): restore the two `.bak-{service,timer}` files → daemon-reload →
enable. Note: rollback restores the unit as-was (still pointed at the removed script) — it
undoes our change, not the pre-existing 203/EXEC bug.

### 2. night-watch.sh → night-watch.sh.retired.wq825
Built 2026-08-22 03:35 for one overnight run; stopped 2026-08-23 (896 cycles / 12h). No
systemd unit (`is-enabled` = not-found), no timer, no cron, no running process, no ExecStart
reference anywhere. Its two roles are both replaced: the dispatcher-stall ALARM by wq#825
(`dispatcher_health.py`, whose docstring cites night-watch's 600-loop failure as its motivating
incident), and the dispatch-TRIGGER loop by `lumen-foreman-dispatch.timer` (the single driver
since night-watch was stopped — see OPERATIONS_INVENTORY O2). Renamed, not deleted.

### 3. chain-runner.sh → chain-runner.sh.retired.wq715
Built 2026-08-21 to bridge overnight gaps before the dispatcher existed. The foreman
auto-dispatcher (wq#715, done 2026-08-21) supersedes it. Only reference fleet-wide is a
historical comment in `agent/lumen_self_healer.py:169` (explaining that chain units used to run
fworker inside them, so the healer scans process cmdlines) — not an invocation. No unit, no
timer, no cron, no live process. Renamed, not deleted; the healer's defensive cmdline scan is
unaffected (fewer such cmdlines, not a break).

## Correction to the record
The wq#924 kickoff and the R3 problem statement both cited **wq#829** as dispatch-deadman's real
fix. That is wrong: **wq#829** is the MCP-gateway-flap ticket (unrelated). The real fix is
**wq#825** ("[ops] night-watch: alarm when the dispatcher itself is not dispatching", done
2026-08-23) — confirmed by the `.retired.wq825` rename, the `lumen-foreman-health.service`
header ("Replaces the interim dispatch-deadman.sh"), and wq#997's evidence. Not propagating
wq#829.

## New defects found → tickets filed
- **wq#1012 [build, pri 1]** — dispatcher-health watcher DUPLICATED. `lumen-foreman-health.timer`
  (5-min standalone, `dispatch_health.py`) AND `self_heal.py` in-process `dispatcher_health`
  (10-min) are BOTH live and BOTH write `health_state.json`. wq#997 called the standalone one
  "dead code to delete in follow-up" — it is NOT dead (timer enabled+active) and it also carries
  wq#845 config-lint. Needs designed consolidation; do NOT blind-delete it.
- **wq#1013 [mission-control, pri 2]** — `fleet-telemetry` (wq#744) labelled "superseded by
  node_exporter (wq#753)", but node_exporter feeds Grafana/VM only; `mission-control/app.py` reads
  `fleet_telemetry.json` as its SOLE fleet source. Either migrate MC to VM then retire, or drop
  the misleading label.

## Keep + labelled (no retirement)
- `etc/plug-recover.conf` — `ARMED=0` with explicit "Do NOT set ARMED=1 until wq#390 policy
  decision" comment. Correctly disarmed + labelled. wq#390 (fleet hardware-watchdog arming) is an
  OPEN Frank-decision ticket — a genuine physical/risk policy call, not an interim to retire.
- instinct rule "interim until wq#715 dispatcher" (instincts.yaml:318) — wq#715 is done; rule
  already flagged for re-scope by A7 §4.1 #38 with Frank as named retirer. Instincts are
  proposal-only (nothing auto-edits instincts.yaml) — left for that path, not edited here.

## Thor
Not touched. No command in this session targeted 100.112.151.100.
