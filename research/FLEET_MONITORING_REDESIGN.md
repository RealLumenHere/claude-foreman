# FLEET MONITORING REDESIGN

Research + design only. No changes made. Verified live on lumen 2026-09-11 (PDT).
Raw inventory: `/home/lumen/scratch/monaudit/inventory.json` (139 units, full systemd properties).

Prior art this builds on — read these first, do not re-derive:
- `/home/lumen/foreman/research/FLEET_SERVICE_ATLAS.md` (wq#1627, PR#410 `2f07653`) — already
  identified the guard exit-code defect as a class defect, §5 item 1.
- `ops/wq1657-guard-exit-convention/DESIGN.md` in claude-foreman (wq#1657, PR#417 `cbe0bd1`,
  merged 2026-09-10) — the fix for defect #2 is written, tested and merged. **Not deployed.**
- wq#1679 (status: open) — the deploy ticket for that fix.
- wq#1724 — class ticket: "ops/ artifact closed done, never deployed." This is the meta-root-cause
  behind the monitoring layer rotting; see §F.

---

## CORRECTIONS TO THE BRIEF

Three of the stated facts do not survive verification. They matter because two of them change the
recommendation.

**1. "~91 units (34 timers + 57 services)" — undercount.** Keyword sweep
(`guard|watch|sentinel|probe|verif|scan|health|monitor|check|reap|drift|canary|alert|audit|smoke|
heartbeat|sweep|pulse|patrol|doctor|selftest|nanny|keeper`) over `systemctl list-unit-files`
returns **139 units: 77 services, 61 timers, 1 path**. 13 of those are stock Ubuntu/systemd
(`auditd`, `audit-rules`, `e2scrub_reap`, `lvm2-monitor`, `mdcheck_start`, `mdcheck_continue`,
`mdmonitor`, `mdmonitor-oneshot`, `modprobe@`, `systemd-battery-check`,
`systemd-boot-check-no-failures`, `systemd-quotacheck-root`, `systemd-quotacheck@`). That leaves
**~126 fleet-authored self-monitoring units**, plus at least 5 the keyword net missed because they
are alerting *plumbing* rather than monitors: `lumen-failed-units.service/.timer`,
`lumen-aq-escalate.timer/.service`, `lumen-gatus-ntfy-relay.service`. Call it **~130**. The problem
is 40% larger than stated.

**2. "Only ~6 units have OnFailure=" — right order, wrong distribution.** Fleet-wide, exactly
**11 unit files carry `OnFailure=`**:
`/etc/systemd/system/lumen-agent.service.d/40-onfailure.conf`,
`lumen-fleet-cerebras.service.d/40-onfailure.conf`, `lumen-fleet-mistral.service.d/40-onfailure.conf`,
`lumen-fleet-openrouter.service.d/40-onfailure.conf`, `lumen-messenger.service.d/40-onfailure.conf`,
`lumen-scribe.service.d/40-onfailure.conf`, `lumen-dispatch-lint.service`,
`lumen-foreman-gate@.service`, `lumen-pokemon-live.service`, `lumen-pat-scan.service`,
`lumen-retirement-audit.service`.
Nine of those are **workload** services. Only **2 of ~130 monitoring units** (`lumen-pat-scan`,
`lumen-retirement-audit`) have a failure handler — **1.5% coverage, not 7%**. The alerting template
`ntfy-onfailure@.service` (wq#392, `ExecStart=/home/lumen/bin/systemd-onfailure-ntfy %i`) was built
and then applied almost exclusively to the things being watched, not the watchers.

**3. "Gatus is HTTP-endpoint-only, watching just 6 endpoints, architecturally blind" — wrong, and
this is the important one.** Live: `curl http://localhost:8418/api/v1/endpoints/statuses` returns
**65 endpoints in 7 groups** — `0-backhaul` 4, `1-nodes` 21, `2-lumen-services` 11, `3-inference` 1,
`4-tunnels` 2, `5-probes` 18, `6-models` 8. Config: `/home/lumen/sentinel/config/config.yaml`
(20 KB, config-as-code, in restic scope), container `sentinel-gatus`, up 11 days.

More to the point, **the exec/push pattern already exists and is already in production.**
`config.yaml:253` opens an `external-endpoints:` block. 26 of the 65 endpoints (all of `5-probes`
and `6-models`) are push-mode, fed by `/home/lumen/sentinel/probes/sentinel_probes.py`
(`push_gatus()`, line 66; key format `5-probes_<name>`, bearer token `sntl-<redacted>`), driven
by `sentinel-probes.timer` every 30 min. Each carries a `heartbeat: {interval: N}` — Gatus fails the
endpoint automatically if no push arrives in the window. The config comment on
`probe-output-rate` says it out loud: *"missed report = DOWN (dead-man switch on the probe itself)"*.

So Gatus is not the blind hub. **The dead-man-switch mechanism the fleet needs is already deployed,
proven, and running — on 26 hand-registered subjects out of ~130.** The gap is enrolment, not
capability. That single fact collapses most of §C.3 into a config-generation problem.

---

## A. HARDWARE vs SOFTWARE

**Purely software/architecture. Confirmed, with evidence, not inference.**

Every hardware-signal path on this box is healthy and reporting:

| Signal | Unit / source | State 2026-09-11 |
|---|---|---|
| Thermal/power shutdown | `lumen-guardian.service` (`/home/lumen/bin/lumen-guardian.py`, `Restart=always`) | active/running, Result=success |
| ECC memory (EDAC CE/UE delta) | `lumen-ecc-watch.service/.timer` (`/home/lumen/bin/lumen-ecc-watch`) | enabled, active, last run clean |
| MD array | `mdmonitor.service`, `mdcheck_start.timer` | active/running, no degrade |
| Host load | `lumen-load-watch.service/.timer` | enabled, active, clean |
| Network backhaul | Gatus `0-backhaul` (4 endpoints: GL-BE9300 wwan, TP-Link gw, 1.1.1.1 ICMP, 8.8.8.8 DNS) | 4/4 `success=true` |
| Node reachability | Gatus `1-nodes` (21 endpoints) | 19/21 up; 2 down are `3dprinter` and `khepri`, both expected-offline hardware, not Lumen |

Not one of the reported failure modes has a hardware fingerprint:
- The 6 currently-failed units all exit `status=1` from a Python or bash **detector**
  (`Result=exit-code`, `ExecMainStatus=1`) — that is a program returning a value, not a fault.
- The OAuth-logout stall was a **credential state change**, not a device.
- The 119 fire-mode crashes were a **loop without a circuit breaker**.
- Dead guards staying dead is **`Restart=no` plus no supervisor** — a policy, not a component.

Verdict: this is architecture. Do not spend a dollar of hardware on it. Corollary: the fix is also
free — every mechanism needed is already installed (systemd, Gatus, ntfy, aq).

---

## B. INVENTORY + COVERAGE MAP

### B.1 Classification (139 keyword-matched units)

| Axis | Count |
|---|---|
| Services | 77 (13 stock OS → **64 fleet-authored**) |
| Timers | 61 (3 stock → **58 fleet-authored**) |
| Path units | 1 (`dc-config-guard.path`) |
| `Type=oneshot` services | **64 of 77 (83%)** |
| `Restart=no` services | **68 of 77 (88%)** |
| Long-running daemons | 9 (7 fleet-authored, 2 stock) |
| Services with `OnFailure=` | **2 of 77** (`lumen-pat-scan`, `lumen-retirement-audit`) |
| Services with `SuccessExitStatus=` set | **6** — `git-guard-sentinel` (1), `lumen-public-guardian` (0 1), `lumen-repo-sweep` (1), `lumen-ui-drift` (0 1), `repo-visibility-sentinel` (1), `unit-drift` (0 1) |

The 7 fleet-authored long-running monitors: `lumen-alert-queue` (notify, Restart=always, **active**),
`lumen-guardian` (always, **active**), `lumen-litellm-auth-monitor` (always, **active**),
`lumen-monitor` (always, **active**), `n8n-watcher` (always, **active**),
`lumen-smart-watchdog` (always, **inactive/dead**), `lumen-gatus-ntfy-relay` (**active**).

### B.2 Behavioural taxonomy

- **Detection probe** (runs, reports a finding, changes nothing): `lumen-pat-scan`, `wq-drift-guard`,
  `unit-drift`, `drift-assert`, `storage-manifest-drift`, `lumen-ui-drift`, `lumen-contradiction-scan`,
  `lumen-state-verify`, `lumen-memory-audit`, `lumen-model-audit`, `lumen-predicate-audit`,
  `lumen-sqlite-health`, `lumen-backup-verify`, `lumen-read-dir-sweep`, `commit-hygiene-sweep`,
  `primary-tree-guard`, `repo-visibility-sentinel`, `git-guard-sentinel`, `lumen-repo-sweep`,
  `gitea-version-check`, `langfuse-evaluator-smoke`, `sentinel-probes`, `lumen-alert-hygiene-audit`,
  `lumen-retirement-audit`, `fleet-auth-ssh-probe`, `gatus-sshauth-probe`, `lumen-failed-units`.
- **Guard/reaper** (detects *and* remediates): `dc-config-guard` (re-enforces DC tier-1 config),
  `mcp-pressure-guard` (wq#380 auto-recovery), `lumen-embed-guard` (integrity **+ repair**),
  `dispatch-watchdog` (stall → auto-recover → page), `lumen-rerank-thread-watch` (**auto-rollback**),
  `wadjet-ha-monitor` (**auto-promote** vault failover), `lumen-smart-watchdog` (liveness restarts),
  `lumen-guardian` (threshold **shutdown**).
- **Alerter** (consumes a signal, pages): `litellm-auth-alert`, `lumen-budget-alert`,
  `langfuse-idle-alert`, `lumen-split-watch`, `n8n-watcher`, `lumen-alert-queue`,
  `lumen-gatus-ntfy-relay`, `lumen-aq-escalate`, `alert-deadman-beat`.
- **Meta-watchdog** (watches another monitor): `lumen-sentinel-watch` (curls Gatus :8418, pages if
  the Sentinel itself is down), `alert-deadman-beat` (wq#1039, off-fleet dead-man on the alert path),
  `dispatch-watchdog`, `lumen-foreman-health`, `lumen-model-watchdog`. **Five point-checks. Nothing
  watches the class.**
- **Not monitoring at all** — intel/business feeds sitting in the monitoring namespace and inflating
  its apparent size: `lumen-ebay-watch`, `lumen-bc250-watch`, `lumen-community-watch`, `jay-watch`,
  `lumen-github-watch`, `lumen-pr-watch`, `lumen-bench-checker`. **7 units.** They page the same ntfy
  topic as real faults, which is a direct contributor to alert-blindness.

### B.3 The redundancy count — over-built

Collapsing ~130 units onto the things they actually watch gives **~13 subject clusters and roughly
32 genuinely-distinct monitored facts**. Multiplicity ≈ **4x**.

| # | Subject cluster | Units / endpoints on it | Distinct facts |
|---|---|---|---|
| 1 | Git & repo hygiene | `commit-hygiene-sweep`, `git-guard-sentinel`, `primary-tree-guard`, `lumen-repo-sweep`, `repo-visibility-sentinel`, `lumen-pr-watch`, `lumen-github-watch`, `jay-watch` (8) | 4 |
| 2 | Config / state drift | `drift-assert`, `dc-config-guard`(+`.path`), `unit-drift`, `storage-manifest-drift`, `lumen-ui-drift`, `wq-drift-guard`, `lumen-contradiction-scan`, `lumen-state-verify`, `lumen-predicate-audit` (10) | 4 |
| 3 | Inference / LiteLLM health | `lumen-model-watchdog`, `lumen-model-audit`, `lumen-budget-alert`, `litellm-auth-alert`, `lumen-litellm-auth-monitor`, `langfuse-idle-alert`, `langfuse-evaluator-smoke`, Gatus `6-models` ×8, `probe-synthetic-llm`, `2-lumen-services/litellm` (16) | 4 |
| 4 | Dispatch / foreman | `dispatch-watchdog`, `lumen-foreman-health`, `lumen-foreman-health-window`, `lumen-split-watch`, `a5-health` (5) | 2 |
| 5 | Memory / embeddings | `lumen-embed-guard`, `lumen-memory-audit`, `probe-minerva-rates`, `probe-state-drift` (4) | 2 |
| 6 | Node & auth liveness | Gatus `1-nodes` ×21, `fleet-auth-ssh-probe`, `gatus-sshauth-probe`, `wadjet-ha-monitor`, `maat-watch`, `probe-maat-vault` (26) | 3 |
| 7 | LPP / Pokemon | `lpp-watchdog`, `lumen-lpp-sentinel`, `probe-pokemon-liveness`, `lumen-public-guardian` (4) | 1 |
| 8 | Secrets exposure | `lumen-pat-scan`, `lumen-read-dir-sweep` (2) | 2 |
| 9 | Backups | `lumen-backup-verify`, `probe-backup-freshness` (2) | 1 |
| 10 | Storage | `probe-storage-integrity`, `storage-manifest-drift`, `lumen-sqlite-health` (3) | 2 |
| 11 | Host hardware | `lumen-guardian`, `lumen-ecc-watch`, `lumen-load-watch`, `mdmonitor*`, `0-backhaul` ×4 (9) | 4 |
| 12 | Alert-path self-check | `lumen-sentinel-watch`, `alert-deadman-beat`, `lumen-alert-hygiene-audit`, `lumen-aq-escalate`, `lumen-gatus-ntfy-relay` (5) | 2 |
| 13 | Housekeeping sweeps | `lumen-scratch-sweep`, `lumen-retirement-audit`, `lumen-failed-units` (3) | 1 |

Clusters 1, 2, 3 and 6 carry 60 units between them for 15 facts. Cluster 3 alone has **16 separate
things** answering one question — *"can Lumen do inference right now?"*

### B.4 The blind spots — under-covered, at the same time

`systemctl --failed` shows 6. Live enumeration of the monitoring class shows **15 units not doing
their job**, of which **9 are invisible to every existing surface**:

**Failed and visible (6):** `fleet-inventory-render.path`, `langfuse-evaluator-smoke.service`,
`lumen-pat-scan.service`, `lumen-research-index.service`, `lumen-state-verify.service`,
`wq-drift-guard.service`. Of these, `lumen-pat-scan` and `wq-drift-guard` are **working correctly** —
they exit 1 because they found something (defect #2).

**Disabled monitors — silent coverage loss, invisible to `systemctl --failed` (7):**
`a5-health.timer`, `lpp-watchdog.timer`, `lumen-brain-health.timer`, `lumen-lpp-sentinel.timer`,
`lumen-predicate-audit.timer`, `lumen-watchdog.timer`, `maat-watch.timer`. All
`UnitFileState=disabled`, `ActiveState=inactive`, `LastTriggerUSec` absent. Nothing anywhere says
"the thing that was watching X stopped existing." `lumen-watchdog.timer` — the unit literally named
*"Lumen Homelab Watchdog"* — is one of them.

**Stopped daemon, not failed (1):** `lumen-smart-watchdog.service` has `Restart=always` and is
`inactive/dead`. `Restart=` only applies to a process that *exits*; a unit that was stopped is not
restarted and is not failed. It is the agent-stall + RAM monitor. It is off. Nothing reports this.

**Enabled, active, never fired (1):** `storage-manifest-drift.timer` — `UnitFileState=enabled`,
`ActiveState=active`, **no `LastTriggerUSec`**. A daily 06:40 UTC drift check with no record of ever
having run.

**Stale Gatus pushes right now (3):** `probe-dead-references` (last push 2026-09-11T00:37Z, 26h
heartbeat), `probe-invisible-work-watch` (2026-09-10T21:33Z, 7d gate), `probe-synthetic-llm`
(2026-09-11T16:52Z, 5h heartbeat) — all currently `success=false`. The dead-man mechanism **is
firing correctly**; these are real, open, and on the dashboard nobody is looking at.

**The class blind spot:** no unit anywhere computes health over *the monitoring layer as a set*.
`lumen-failed-units` (`/home/lumen/bin/lumen-failed-units`, 5-min timer, edge-triggered → ntfy + aq)
is the closest thing and was built for exactly this reason (logrotate failed 6 days unnoticed,
2026-08-08→13). But it answers **one** of four necessary questions — *is it failed?* — and by design
alerts **once** on the edge and then goes quiet. A guard that dies on Monday pages once on Monday and
is silent forever after. That is precisely the reported symptom: *"guard services die and nobody's
told."* They were told. Once. Nine days ago. Into a queue that now holds **274 open rows**
(`aq stats`: 274 open / 198 acked / 739 resolved).

**Summary: over-built ~4x on 13 crowded subjects, and structurally blind to four of the five ways a
monitor actually dies.** Only "crashed" is covered. "Disabled", "stopped", "never ran", and "ran but
its output goes nowhere anyone reads" are not.

---

## C. THE THREE ROOT DEFECTS

### C.1 NO SUPERVISION — 88% `Restart=no`, 98.5% no `OnFailure=`

The framing "add Restart= or add OnFailure=" is too narrow, because both only fire on **exit-with-
failure**, and §B.4 shows that is 1 of 5 death modes. Evaluate against all five.

| Option | Catches crashed | Catches disabled | Catches stopped daemon | Catches never-fired | Catches stale-output | Cost |
|---|---|---|---|---|---|---|
| (a) `Restart=on-failure` + `StartLimitIntervalSec`/`Burst` per unit | partial | no | no | no | no | per-unit drop-ins, 130 files |
| (b) universal `OnFailure=ntfy-onfailure@%n` | yes | no | no | no | no | 1 drop-in |
| (c) meta-supervisor timer over the class | yes | yes | yes | yes | yes | 1 new oneshot + timer |

**(a) is wrong for 83% of the layer and should be scoped hard.** 64 of 77 services are
`Type=oneshot`. For a timer-driven oneshot, `Restart=` is the wrong lever — the *timer* is what
schedules the next run, and `Restart=on-failure` on a detector that exits 1 on a finding produces a
restart loop that re-runs the detection continuously. That is how you manufacture a second
119-crash incident. Apply (a) **only to the 7 long-running daemons**, and only with limits:

```
[Unit]
StartLimitIntervalSec=300
StartLimitBurst=5
[Service]
Restart=on-failure
RestartSec=10s
```

Without `StartLimitIntervalSec`/`StartLimitBurst`, `Restart=` is a restart-storm generator; with
them, systemd gives up after N tries and leaves the unit failed — which is fine, because (c) then
sees it. Current best practice is unambiguous that unbounded restart is worse than no restart:
*"too aggressive and you create a restart storm that worsens an underlying problem"*
(oneuptime.com, 2026-03-02, RestartSec/WatchdogSec on Ubuntu).

**(b) is cheap, necessary, and not sufficient.** systemd supports a top-level drop-in at
`/etc/systemd/system/service.d/*.conf` that applies to **every** service on the box — one file
extends wq#392's `ntfy-onfailure@.service` fleet-wide. Two hazards, both solvable:
1. **Recursion.** The drop-in would also apply to `ntfy-onfailure@.service` itself; if the notifier
   fails it invokes itself. Neutralise with `/etc/systemd/system/ntfy-onfailure@.service.d/
   50-no-onfailure.conf` containing `[Unit]\nOnFailure=` (empty resets the list).
2. **Blast radius.** It also covers stock OS units. That is arguably a feature — the logrotate miss
   of 2026-08-08 was a stock unit — but it will raise volume before Phase 1 has cut the noise. Land
   (b) *after* the cluster consolidation, not before.

**(c) is the backbone and the only option that covers the observed failure modes.** Design in §D.2.

**Recommendation: (c) primary, (b) second, (a) narrowly scoped to the 7 daemons.** Not either/or —
(b) gives sub-second notification on crash, (c) gives ≤5-minute detection on everything else, and
(a) gives free recovery on the daemons. Depth, not choice.

A note on the systemd-native watchdog (`WatchdogSec=` + `sd_notify(WATCHDOG=1)`): correct tool,
wrong fleet shape. It requires a long-running `Type=notify` service that feeds a heartbeat at
`WatchdogSec/2`. Exactly **one** unit here is `Type=notify` (`lumen-alert-queue`). Adopting it means
rewriting 64 oneshots into daemons — a strictly worse architecture for periodic detection work.
Use `WatchdogSec` on `lumen-alert-queue` (it is the alert path's single point of failure and it is
already `Type=notify`) and nowhere else.

### C.2 SIGNAL CONFUSION — `exit 1` means both "found it" and "broke"

**This is already solved in code. It is undeployed. Do not redesign it.**

The defect, verified live: of 6 failed units, **2 are healthy detectors doing their job** —
`lumen-pat-scan` (`/home/lumen/bin/wq523_pat_residue.py --alert`, exits 1 on token residue) and
`wq-drift-guard` (`/home/lumen/bin/wq-drift-guard`, exits 1 on divergence from git HEAD). The fleet
has no convention, and the inconsistency is visible in the unit files:

- **exit 1 = finding, unit shows failed:** `lumen-pat-scan`, `wq-drift-guard`, `lumen-budget-alert`,
  `lumen-retirement-audit`, `lumen-dispatch-lint`.
- **exit 1 = finding, masked with `SuccessExitStatus=`:** `git-guard-sentinel` (1),
  `repo-visibility-sentinel` (1), `lumen-repo-sweep` (1), `lumen-public-guardian` (0 1),
  `lumen-ui-drift` (0 1), `unit-drift` (0 1). Note `lumen-repo-sweep` and `lumen-ui-drift` both show
  `ExecMainStatus=1` with `Result=success` right now — working as intended, invisibly.
- **exit 0 always + report via ntfy/aq:** `dispatch_health.py`, `lumen-failed-units`
  (`SuccessExitStatus=0`, comment: *"Reporting-only tool; never let its own failure create a failed
  unit"*), `lumen-load-watch`, `litellm-auth-alert`.

Three conventions coexisting is the same as none. The consequence is the one that matters: red
becomes wallpaper, and a real crash hides among working guards.

**The shipped fix** (wq#1657, PR#417 `cbe0bd1`, merged 2026-09-10, 12 py tests + offline bash
harness passing):
- **Mode A (preferred):** guard always exits 0, reports findings via `ntfy` + `aq`. The
  `lumen-load-watch` template.
- **Mode B (compatibility):** guard exits 1 *and* declares `X-Lumen-Guard=finding-exit` in `[Unit]`
  *and* `OnFailure=ntfy-onfailure@%n`, so a finding routes somewhere real instead of rotting.
- **GEL1/GEL2 lint** wired into `unit-drift` so a new guard cannot land without picking a mode.
- **finding-exit classifier + 10-guard allowlist** in `lumen-failed-units`, so it reports
  *"guard fired"* (info, aq) versus *"guard crashed"* (urgent, page) instead of conflating them.

Deploy order is written out in `ops/wq1657-guard-exit-convention/DESIGN.md`, tracked as **wq#1679,
status open, unclaimed since 2026-09-10**. Verification criterion is already specified: the next
`lumen-budget-alert` finding must produce an aq row with source `guard-fired` (info), not
`failed-unit` (high).

**Recommendation: deploy wq#1679 unchanged.** Target state is Mode A everywhere; Mode B is the shim
that buys time without leaving findings silent. Do not re-open the design — it was decided, reviewed
and merged nine days ago.

### C.3 THE HUB — not blind, just under-enrolled

Per the corrections above: Gatus sees 65 endpoints, and 26 of them are **push-mode external
endpoints with heartbeat dead-man semantics**, fed by `sentinel_probes.py::push_gatus()`. Gatus
docs confirm the semantic (`/home/lumen/build/gatus-v5.36.0/README.md:336-337`):
*"Heartbeat configuration for monitoring when the external endpoint stops sending updates... If no
update is received within this interval, alerts will be triggered."* The fleet's own config comment
already calls it a dead-man switch on the probe itself.

So the real question is not *"how do we make Gatus see systemd?"* but *"why are only 26 subjects
enrolled when there are ~130?"* Answer: enrolment is manual — each probe is a hand-written Python
function in `PROBES = [...]` (`sentinel_probes.py:967`) plus a hand-written YAML block. It does not
scale to the unit population and it never will.

**Option 1 — generalise the existing push pattern (RECOMMENDED).** One new oneshot reads systemd
state for the whole monitoring class, evaluates four predicates (§D.2), writes findings to `aq`, and
pushes **rolled-up** external endpoints into Gatus with a heartbeat set to 2× its own cadence — so
the supervisor's own death is caught by the hub, by the same mechanism, with no new component.
- Cost: 1 service + 1 timer + ~4 YAML blocks. Zero new daemons, zero new ports, zero new auth.
- Config stays as code in `config.yaml` (already in restic scope and git).
- Reuses a path with 11 days of uptime and proven alerting via `lumen-gatus-ntfy-relay` (wq#1184).
- Roll-up, not per-unit: 130 Gatus endpoints would reproduce the wallpaper problem on a new surface.
  Push 4-6 tier endpoints (`selfmon-supervision`, `selfmon-coverage`, `selfmon-freshness`,
  `selfmon-auth`), with the detail in `aq` where it can be triaged, acked and silenced.

**Option 2 — node_exporter + Prometheus + Alertmanager.** The standard homelab answer, and it does
solve this properly: the `systemd` collector exposes `node_systemd_unit_state{name,state}` so
"how many monitoring units are not active" becomes a query, and Alertmanager's always-firing
`Watchdog` alert is the canonical meta-monitoring dead-man (Prometheus calls this *metamonitoring*;
Tor Project TPA-RFC-33 and the prometheus-operator `Watchdog` rule are the reference implementations).
**Reject for this fleet.** It is an ecosystem, not a tool — TSDB + scrape config + rule files +
Alertmanager routing + a second alerting path to keep in sync with ntfy/aq. For a **single
operator** whose presenting problem is *too many moving parts that rot unnoticed*, adding three
long-running services and a second alert pipeline makes the disease worse. Current comparative
guidance agrees on the threshold: *"If you only need endpoint uptime monitoring, Prometheus is
overkill"* and under ~200 endpoints a single lightweight monitor wins (dev.to, 2026-06-13; talos.tools
Gatus-vs-Kuma, 2026-04-21). Host metrics are also already covered — `beszel-hub` is running and in
Gatus `2-lumen-services`. Revisit only if the fleet needs real time-series analysis, not alerting.

**Option 3 — purpose-built lightweight aggregator** reading `systemctl --failed` + aq into one pane.
This is `lumen-failed-units` + `aq` + a renderer, i.e. **~80% already built**. The right move is not
to build a new one but to (i) widen its predicates from 1 to 4 and (ii) stop treating edge-triggered
notification as a substitute for a standing state view. Fold into Option 1: the aggregator is the
data layer, Gatus is the pane. Building a *separate* dashboard would make two panes, and §D's whole
point is that there must be exactly one.

**Uptime Kuma / Healthchecks — evaluated, rejected.** Uptime Kuma's push monitors would work but its
state lives in SQLite with no version-controllable config, which is a regression from Gatus's
config-as-code (already git-tracked, already restic-scoped, `unit-drift` already lints
`/etc/systemd/system` against git — the fleet's whole discipline is desired-state-in-git).
Healthchecks.io is purpose-built for exactly the missing semantic — *"catching the tasks that fail
silently"*, cron and backup jobs that quietly stop running (selfhostpicks.com, 2026-06-30) — but
Gatus external-endpoint heartbeat already **is** that feature, in production, on 26 subjects. Adding
Healthchecks would be paying a migration to acquire a capability the fleet already has.

**Consul health checks — rejected.** Service-mesh machinery for a service mesh this fleet does not
run.

---

## D. THE TARGET ARCHITECTURE

### D.1 The hierarchy

```
T0  OPERATOR PANE            Gatus :8418  — the ONE place. 8 groups.
    (the only surface)       new group 7-selfmon (4-6 rolled-up endpoints, heartbeat-backed)
                             new group 7-auth   (one endpoint per deauthable dependency)
         ^ push (bearer, heartbeat = 2x cadence)
         |
T1  TRIAGE / STATE           aq  (/home/lumen/bin/aq -> agents/alert-queue/aq.py)
    (the detail layer)       dedup on (source,key); silence; group; escalate via lumen-aq-escalate
         ^ writes
         |
T2  META-SUPERVISOR          sentinel-unit-health.service/.timer   [NEW — one unit]
    (watches the watchers)   4 predicates over the whole monitoring class, every 5 min
         ^ reads systemd
         |
T3  THE MONITORS             ~130 units. Unchanged in function; tagged, supervised, signal-corrected.
    (unchanged, tagged)      X-Lumen-Monitor=<tier> | X-Lumen-Guard=finding-exit | X-Lumen-Feed
         ^ systemd
         |
T4  SYSTEMD-NATIVE           universal OnFailure -> ntfy-onfailure@%n  (crash, sub-second)
                             Restart=on-failure + StartLimit on the 7 daemons
                             WatchdogSec on lumen-alert-queue only
         |
T5  OFF-BOX DEAD MAN         alert-deadman-beat (wq#1039) — already exists. Extend to assert that
                             T2 pushed within its window, so "the whole box went quiet" still pages.
```

**What consolidates:** clusters 1, 2, 3 and 6 from §B.3 (60 units → target ~20). Cluster 3 in
particular: 16 units answering "can Lumen infer?" should become one probe plus the 8 model-rung
heartbeats that already exist.
**What stays untouched:** the 8 remediating guards (`dc-config-guard`, `mcp-pressure-guard`,
`lumen-embed-guard`, `dispatch-watchdog`, `lumen-rerank-thread-watch`, `wadjet-ha-monitor`,
`lumen-smart-watchdog`, `lumen-guardian`). They do work, not just reporting; consolidating them
would delete capability. They get supervised, not merged.
**What leaves the namespace:** the 7 intel feeds (§B.2). Tag `X-Lumen-Feed=true`, route to a
separate ntfy topic, exclude from health math. They are not faults and must stop competing with
faults for attention.
**What gets decided:** the 7 disabled monitors. Each is either re-enabled or retired to
`.disabled.bak` per the 2026-05-18 cleanup-bundle precedent. "Disabled but present" is the state
that lies to you.

### D.2 The one new component

`sentinel-unit-health.service` (oneshot, `SuccessExitStatus=0`, Mode A: always exits 0) +
`.timer` (`OnUnitActiveSec=5min`). Proposed path `/home/lumen/bin/sentinel-unit-health`.

Enrolment is **derived, never a second list**: it reads `[Unit] X-Lumen-Monitor=` off the unit files
themselves. A new monitor is enrolled by existing, and `unit-drift`'s GEL lint (already shipped in
PR#417) is the gate that refuses an untagged one. This is the same tag mechanism wq#1657 introduced
— reuse it rather than inventing a parallel registry that drifts.

Four predicates per enrolled unit — this is the whole point, since today only #1 is computed:

1. **CRASHED** — `ActiveState=failed` AND not classified finding-exit. → aq `unit-crashed`, urgent.
2. **COVERAGE LOST** — `UnitFileState != enabled` where the manifest expects enabled, or a
   `Restart=always` daemon with `ActiveState != active`. Catches all 7 disabled timers **and**
   `lumen-smart-watchdog`. → aq `monitor-disabled`, high.
3. **STALE** — `LastTriggerUSec` older than 2× declared cadence, or absent on an enabled+active
   timer. Catches `storage-manifest-drift.timer` (never fired) and any silently-wedged timer.
   → aq `monitor-stale`, high.
4. **OUTPUT DEAD** — the unit last ran clean but its downstream sink (Gatus push, aq row, state
   file) has no write inside the window. Catches "runs fine, reports into a void."

Output contract: findings → `aq` (deduped, silenceable, triageable); a rolled-up verdict per tier →
Gatus external endpoint with `heartbeat: {interval: 10m}`. **If `sentinel-unit-health` itself dies,
Gatus fails `selfmon-*` within 10 minutes and pages via `lumen-gatus-ntfy-relay`.** The supervisor is
supervised by the same mechanism it uses, which is the property the current layer lacks entirely.

Standing state, not edge-triggered: unlike `lumen-failed-units`, T2 re-asserts a persisting condition
into Gatus every cycle. `aq`'s existing dedup, `aq silence` and `aq group` are what keep that from
becoming noise — the fatigue control lives in the triage layer where it can be tuned, not in the
detector where "alerted once on Monday" becomes permanent silence.

### D.3 How a NEW failure mode gets caught — the OAuth logout, re-run

Worth being exact, because the honest answer is that T2 alone would **not** have caught it. The
OAuth-dependent unit never failed. It was `active`, it had run recently, it exited 0. Predicates
1-3 all pass. Only predicate 4 has a chance, and only if that path had a registered sink.

The design answer is therefore a **rule**, not just a component:

> Every dependency that can silently de-authenticate — OAuth session, API key, bearer token, SSH
> key, cloud credential — gets its own auth-liveness probe pushing a Gatus external endpoint in
> group `7-auth` with a heartbeat. Absence of a fresh success is a failure. Presence of "no errors"
> is not a success.

This is not novel here; it is **already the pattern, applied unevenly**:
- `fleet-auth-ssh-probe.service` — *"Fleet synthetic AUTH-layer SSH probe (closes monitoring_gap;
  fail-loud)"* — SSH auth, covered.
- `gatus-sshauth-probe.service` → Gatus `5-probes/sshauth-probe` + 6 per-node `*-sshauth` endpoints
  in `1-nodes` — covered.
- `lumen-litellm-auth-monitor.service` (wq#721) + `litellm-auth-alert` (wq#911) — LiteLLM auth,
  covered.
- The Claude/OAuth session behind the MCP gateway path — **not covered**. That is the gap, and it is
  a missing enrolment, not a missing mechanism.

So in the new design the OAuth logout is caught in ~10 minutes by a `7-auth` heartbeat going stale,
and the *class* of "fleet is up but doing no work" is caught independently by the freshness probes
that already exist (`probe-output-rate`, `probe-queue-health`, `probe-invisible-work-watch`,
`dispatch-watchdog`) once T2 guarantees they are themselves alive. Two independent paths, which is
the correct answer for a mode nobody predicted.

The 119 fire-mode crashes over 3 days are the mirror image: high-volume repetition that nobody was
counting. `probe-log-repeats` exists for exactly this; predicate 4 plus a rate condition on the
`7-selfmon` roll-up turns "same thing 119 times" into one standing red on the pane rather than 119
notifications that train the operator to swipe.

---

## E. CURRENT BEST PRACTICE — WEB RESEARCH, AND WHAT APPLIES HERE

| Approach | What it actually solves | Fit for a single-operator, security-conscious, mostly-systemd fleet |
|---|---|---|
| `Restart=on-failure` + `RestartSec` + `StartLimitIntervalSec`/`Burst` | Crash recovery with a storm guard. Consensus: unbounded restart is worse than none. | **Adopt, narrowly** — the 7 long-running daemons only. Meaningless for 64 oneshots. |
| systemd `WatchdogSec=` + `sd_notify(WATCHDOG=1)` | The hung-but-alive case: *"A service that is running but not actually working is often worse than a service that has crashed."* Heartbeat at `WatchdogSec/2`; needs `Restart=on-watchdog`/`on-failure`/`always` to recover. | **Adopt on exactly one unit** — `lumen-alert-queue` (the only `Type=notify`, and the alert path's SPOF). Rewriting oneshots into daemons to use it would be a regression. |
| Generic `systemctl --failed` sweep (vs named-unit allowlists) | Catches units nobody enumerated. | **Already adopted** (`lumen-failed-units`, 2026-08-13, after the 6-day logrotate miss). Needs predicates 2-4 added. |
| Gatus (config-as-code, YAML in git, single Go binary, push-mode external endpoints with heartbeat) | Declarative reachability + condition checks + dead-man on pushed jobs. Rich conditions, first-class git review. | **Already the fleet standard, 11 days uptime, 65 endpoints. Keep and extend.** |
| Uptime Kuma | Point-and-click, push monitors, huge notification breadth. | **Reject** — state in SQLite, no version-controllable config. Regression against a fleet whose discipline is desired-state-in-git (`unit-drift` lints `/etc/systemd/system` against git HEAD). |
| Healthchecks | Purpose-built dead-man for cron/backups — the tasks that fail silently. | **Reject as a new tool** — Gatus `external-endpoints[].heartbeat` already provides this semantic, in production, on 26 subjects. |
| Prometheus + node_exporter + Alertmanager | Real metamonitoring: `node_systemd_unit_state` as a query, plus the canonical always-firing `Watchdog` dead-man alert. The correct answer at scale. | **Reject for now** — an ecosystem, not a tool. Three new long-running services, a TSDB, rule files, and a second alert pipeline to keep in sync with ntfy/aq, for a fleet whose disease is unsupervised moving parts. Host metrics already covered by `beszel-hub`. Guidance is explicit that below ~200 endpoints this is overkill. Revisit only for time-series analysis, not alerting. |
| Consul health checks | Service-registry health in a mesh. | **Reject** — no mesh. |

**Recommendation: stay systemd + Gatus + ntfy + aq. Add exactly one new unit.** The industry
pattern the fleet is missing is not a product, it is a principle, and it appears in every source
consulted: **the alerting pipeline must itself be dead-manned.** Prometheus does it with an
always-firing `Watchdog` alert routed to an external receiver; Uptime Kuma users do it by pointing
Alertmanager at a push monitor; Healthchecks does it as its entire product. This fleet already does
it twice — `alert-deadman-beat` (wq#1039, off-fleet) and `lumen-sentinel-watch` (reciprocal: Lumen
curls Gatus :8418 every 5 min and pages if the Sentinel is down). What is missing is the middle
layer: **nothing dead-mans the ~130 monitors as a class.** That is the one gap, and it is one unit
wide.

---

## F. PHASED MIGRATION

**Phase 0 — DEPLOY WHAT IS ALREADY BUILT (~1h, zero design work)**
Execute wq#1679 per `ops/wq1657-guard-exit-convention/DESIGN.md` (merged PR#417 `cbe0bd1`):
1. `staged/guard-finding-exit.list` → `/home/lumen/bin` (fixes failed-units triage immediately);
2. `staged/lumen-failed-units` → deploy via the bin repo, commit on main, never a dirty edit
   (`primary-tree-guard` will fire otherwise);
3. tag the 10 guard units via drop-ins (`X-Lumen-Guard=finding-exit` + `OnFailure=`), `daemon-reload`,
   `unit-drift` snapshot;
4. `staged/guard_exit_lint.py` + `staged/unit-drift`.
Verify: next `lumen-budget-alert` finding produces aq source `guard-fired` (info), not `failed-unit`
(high). Expected side effect: `systemctl --failed` drops from 6 to ~4 as `lumen-pat-scan` and
`wq-drift-guard` reclassify from "crashed" to "fired".

**Phase 1 — CONSOLIDATE (before adding any supervision)**
- Tag the 7 intel feeds `X-Lumen-Feed=true`, move to a separate ntfy topic, exclude from health math.
- Resolve the 7 disabled monitors: re-enable or retire to `.disabled.bak` (2026-05-18 cleanup-bundle
  precedent). No unit stays in "disabled but present."
- Fix `lumen-smart-watchdog` (stopped `Restart=always` daemon) and `storage-manifest-drift.timer`
  (enabled, active, never fired) — both are live coverage gaps today.
- Collapse clusters 1/2/3/6 (§B.3): 60 units → ~20. Cluster 3 first — 16 units for one question.
- Drain `aq`: 274 open rows is a landfill, and a pane built on it inherits that.
Rationale for ordering: supervising 130 units before cutting to ~85 means building the supervisor
against noise, and landing the universal `OnFailure` drop-in before the cut would spike volume.

**Phase 2 — SUPERVISE**
- Ship `sentinel-unit-health.service/.timer` with the 4 predicates (§D.2). Heartbeat-backed push to
  Gatus `7-selfmon` so it supervises itself.
- Universal `/etc/systemd/system/service.d/50-onfailure.conf` → `ntfy-onfailure@%n`, **with** the
  recursion guard `/etc/systemd/system/ntfy-onfailure@.service.d/50-no-onfailure.conf` (`OnFailure=`).
- `Restart=on-failure` + `StartLimitIntervalSec=300` / `StartLimitBurst=5` on the 7 daemons.
- `WatchdogSec=` on `lumen-alert-queue` only.

**Phase 3 — FIX SIGNALS FOR GOOD**
- Make GEL1/GEL2 lint in `unit-drift` a hard gate: no new guard lands without declaring Mode A or B.
- Migrate Mode-B guards to Mode A (exit 0 + ntfy + aq) as each is next touched. No big-bang rewrite.
- Retire the ad-hoc `SuccessExitStatus=` masks on the 6 units using them — they are Mode B without
  the tag or the `OnFailure`, i.e. findings that go nowhere.

**Phase 4 — UNIFY THE HUB**
- Gatus group `7-auth`: one heartbeat endpoint per deauthable dependency. **Start with the
  Claude/OAuth session** — the one that actually bit.
- Gatus group `7-selfmon` roll-ups as the operator's single pane; `aq` as the detail behind it.
- Extend `alert-deadman-beat` to assert T2 pushed within its window.
- Delete `lumen-sentinel-watch`'s redundancy once `7-selfmon` covers it, or keep as the deliberate
  reciprocal — decide explicitly, do not leave both by default.

---

## G. RECOMMENDATION — SINGLE HIGHEST-LEVERAGE FIRST MOVE

**Deploy wq#1679. Today. Before anything else in this document is designed further.**

Not because it is the biggest fix — it is not; `sentinel-unit-health` is the biggest fix — but
because it is a **prerequisite** and it is **already paid for**. The work is written, reviewed,
tested (12 py tests + offline bash harness) and merged as PR#417. It costs about an hour of
deployment. And until "guard fired" is machine-distinguishable from "guard crashed", every
supervisor built on top inherits a poisoned input: a meta-supervisor that restarts or pages on
`ActiveState=failed` would today be firing on `lumen-pat-scan` and `wq-drift-guard`, which are
**working perfectly**. Automating on top of a signal that is wrong 33% of the time makes the
monitoring layer less trustworthy, not more — which is the exact failure mode being fixed.

**Highest-leverage new build, immediately after: `sentinel-unit-health` (§D.2).** One oneshot, one
timer, four predicates, heartbeat-backed so it cannot die quietly. It is the only thing in this
document that addresses the presenting complaint — *dead watchdogs stay dead and the operator finds
out days later* — because it is the only thing that computes health over the monitoring layer **as a
class** rather than checking named units one at a time.

**And one structural finding that outranks both.** wq#1627 produced a 111 KB atlas naming this exact
class defect. wq#1657 designed and merged the fix. wq#1679 has sat open and unclaimed since
2026-09-10, and its own notes say: *"Same class as wq#1632 (ops/ artifact closed done, never
deployed). Class ticket: wq#1724."* The monitoring layer is not degrading because the fleet cannot
diagnose it — it diagnosed it correctly, twice, nine days ago. **It is degrading because designs
land in `ops/` and never reach `/etc/systemd/system`.** Whatever is built next, wq#1724 is the
ticket that determines whether it survives contact with next month. A monitoring redesign deployed
to a repo monitors nothing.

---
*Research only. No changes made to any unit, config, or service. Live verification 2026-09-11 PDT.*
*Evidence: `/home/lumen/scratch/monaudit/inventory.json`, `systemctl show` over 139 units,*
*`http://localhost:8418/api/v1/endpoints/statuses`, `/home/lumen/sentinel/config/config.yaml`,*
*`/home/lumen/bin/lumen-failed-units`, `aq stats`, `wq show 1627|1657|1679`.*
