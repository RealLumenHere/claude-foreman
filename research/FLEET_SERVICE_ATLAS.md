# FLEET SERVICE ATLAS — Lumen (T5810) systemd dependency + failure-mode map

_wq#1627 · captured 2026-09-10 06:35–06:50 UTC (2026-09-09 23:35 PT) by work-1627-7cb790 · read-only audit, no fleet state changed._
_Regenerate the mechanical parts: `python3 tools/service_atlas_dump.py /tmp/<scratch>` then `python3 tools/service_atlas_catalogue.py /tmp/<scratch> > §2`. Hand-written sections (§1, §3–§7) go stale; trust `systemctl` + `handoff verify` over this file for live state._

## 0. Scope, method, headline

**Scope.** Every loaded systemd service, timer and path unit on Lumen: 627 loaded, of which **300 are fleet-authored** (`FragmentPath` under `/etc/systemd/system`): 179 services, 118 timers, 3 paths. Distro units (docker, redis-server, tailscaled, rasdaemon, netdata, ssh…) appear only where a fleet unit depends on them. The 35 docker containers that fleet units depend on are listed in §1.3 because half the real dependency edges terminate in a container, not a unit.

**Method.** `systemctl show` on every unit for `ExecStart, Type, Restart, Requires/Requisite/Wants/BindsTo/PartOf/After/Before, RequiredBy/WantedBy/TriggeredBy, OnFailure, NRestarts, MemoryMax, timer calendar/monotonic spec`, plus the unit file + drop-ins for `OnUnitActiveSec/OnBootSec`, plus the first ~25 lines and every alert/exit call site of 49 guard scripts, plus `journalctl` for the 8 failed units, plus `docker ps -a`, aq open-row histogram, Gatus endpoint states, the healer watch table, the cron-deadman stamp set. Nothing was restarted, reset or edited.

**Headline (read this if nothing else).**

1. **Coupling is deliberately loose and therefore invisible.** Only 4 fleet services carry a hard `Requires=` (the 3 fleet researchers → docker; foreman-gate@ → its slice). Everything else is `After=`/`Wants=` at most. That is correct policy (no cascading stops), but it means *systemd cannot tell you what breaks when X dies* — the real dependency graph is in TCP ports (LiteLLM :4000, ntfy :8080, memoryd :8139, ollama :11434, messenger :8765, Gatus :8418, DC :3100), SQLite files (`queue.db`, `minerva.db`, `alerts.db`) and docker containers. §4 maps those edges by hand.
2. **Two dangling `After=` targets.** `litellm.service` and `lumen-pgvector.service` do not exist (both are docker containers). The 3 fleet researchers, `lumen-convo-learn` and `lumen-research-index` order after a unit systemd has never seen — harmless today, but it means their boot ordering versus LiteLLM/pgvector is *not* what the unit file claims.
3. **The "failed units" list is polluted by design.** 4 of the 8 currently-failed units (`lumen-budget-alert`, `lumen-pat-scan`, `wq-drift-guard`, `primary-tree-guard`) are guards that exit 1 *to signal a finding* — they are working. `lumen-failed-units` then alerts on them as failures, `handoff verify` / `systemctl --failed` readers learn to ignore red, and a real failure hides among them. The fleet has no convention for "guard fired" vs "guard broke" (`dispatch_health.py` explicitly chose exit 0 + ntfy; `retirement-audit` explicitly chose exit 1 so OnFailure fires; `unit-drift` exits 1 silently). §5 item 1.
4. **Alert delivery is one process.** Every alert (ntfy-send, aq, OnFailure hooks, Gatus relay, guardian) converges on the `ntfy` docker container on :8080 and the `lumen-alert-queue` subscriber. That is *covered* off-fleet by the deadman beat (wq#1039) — the beat refuses to beat when ntfy won't accept a publish — so the SPOF is watched, but the *triage* side is not: 250 open aq rows (84 ntfy:lumen-alerts, 78 jay-watch, 37 failed-unit) means the queue Frank is meant to read is already saturated. §5 item 2.
5. **Six timers labelled "installed DISABLED / Frank-gated" are live and doing work** (`lumen-familiarity` ingested 42 rows 01:21Z today; `lumen-model-audit` is on run 64; `lumen-skillhex` ran #15 yesterday). The timers were enabled; the descriptions and `UnitFileState=disabled` on the *service* (irrelevant for a timer-triggered unit) were never updated. Either the gate was bypassed or the label is stale — both are REPEAT-class defects (Frank was told these were off). §7 F-3.
6. **Three guards watch things that no longer exist.** `lumen-split-watch` checks `lumen-planner/generator/evaluator` (not-found) and reports `ok` with 206 items stuck in `lumen5:plan_in` and 187 dead; the healer's `stall` table watches `lumen-agent` (masked); the healer fallback table lists `lumen-prompt-engine`, `lumen-vip-api`, `lumen-vip-watcher` (not-found). Dead guards over dead systems are the exact class that let "drift alerts fire forever" (ticket premise). §7 F-4/F-5.
7. **One silent alert black hole.** `n8n-watcher` posts to topic `lumen_alerts` (underscore). The subscriber consumes `lumen-alerts,lumen-updates` (hyphen). Every n8n workflow failure since the watcher was written has gone to a topic nobody reads. §7 F-6.
8. **`fleet-inventory-render.path` is dead for good** — it hit `start-limit-hit` at 05:39Z when wq#1580 wrote 13 machine YAMLs in ~15 s (each write re-triggers the render; no `StartLimitIntervalSec=0`), and a failed `.path` unit does not self-recover. The 5-min belt-and-braces timer masks it. §7 F-7.
9. **`jay-watch` has failed every 15 min since the wq#1581 token move** ("no GitHub PAT in agent .env") and has 78 open aq rows to show for it — the very path the HANDOFF says is dead. §7 F-8.
10. **Restart-loop candidates:** `lumen-litellm-auth-monitor` NRestarts=8 (dies each time the litellm container recreates — by design, `Restart=always` covers it), `lumen-powerdash-vmtunnel` NRestarts=6 (SSH tunnel to Bes). Neither is alarmed on restart count; `Restart=always` with no `StartLimitBurst` ceiling turns a permanently-broken dependency into an infinite quiet loop. §5 item 7.

## 1. Topology — what actually depends on what

### 1.1 Layers (bottom = if it dies, most things above it degrade)

```
L0  kernel/RAID/net   assemble-md0-data → /data/lumen (md0 RAID5)   assemble-md1-scratch → /scratch (md1 RAID0)
                      storage-boot-assert (manifest vs arrays)   iptables-restore   nic-park   lumen-power/thp/hugepages
L1  distro daemons    docker.service   redis-server   tailscaled   rasdaemon   netdata   ssh
L2  containers        litellm:4000  ntfy:8080  lumen-pgvector  sentinel-gatus:8418  langfuse-{web,worker,pg,redis,ch}
    (docker)          searxng/vane  librechat  dify (11 ctrs)  plausible(3)  mongodb  minio  glance  actual-budget  postgres-mcp×2
L3  ingress/control   cloudflared (ALL public hostnames; ONE tunnel)   desktop-commander-mcp:3100 (Commander)
                      mcp-gateway   memory-mcp:3104   thoth-origin (autossh → Thoth)   lumen-powerdash-vmtunnel (ssh → Bes VM)
L4  product daemons   lumen-messenger:8765  lumen-memoryd:8139  ollama:11434  lumen-rerank  lumen-pokemon-live(:8871)
                      lumen-scribe:8131  lumen-mission-control:8136  lumen-cord:8152  lumen-proposals-ui:8110  lumen-bench:8135
                      lumen-prompt-forge:8792  lumen-copilot-status:8145  lumen-dashboard  projects-app:8124  fleet-status:7070
                      fleet-inventory-web:8419  legal-resource:8130  beszel-hub:8090/agent  n8n  lumen-powerdash
L5  agent lanes       lumen-fleet-{cerebras,mistral,openrouter} (research queue drainers, notify+watchdog)
                      lumen-foreman-dispatch.timer (20 min broker) → detached fworkers → wq queue.db
                      minerva-{classifier,proposal,eval,factory,brief} chain   lumen-intel/scout/signal-engine
L6  guard layer       §3 — ~70 timer-driven probes/guards + 5 daemons (self-healer, guardian, node-killer, alert-queue, auth-monitor)
L7  alert path        ntfy-send → ntfy:8080 (+OOB) → lumen-alert-queue → alerts.db (aq) → aq_escalate → wq fleet-fires
                      ntfy-onfailure@ (OnFailure) · gatus-ntfy-relay:8784 · deadman beat → deadman.lumennode.uk (off-fleet)
```

### 1.2 Declared systemd edges that carry weight

Only these declared edges do anything at stop/start time (the rest are `After=` ordering only):

| edge | kind | effect |
|---|---|---|
| `lumen-fleet-{cerebras,mistral,openrouter}` → `docker.service` | **Requires** | stopping docker stops all 3 researchers |
| `lumen-foreman-gate@{nightly,window}` → `system-lumen-foreman-gate.slice` | Requires | slice only |
| `lumen-powerdash-vmtunnel` → `lumen-powerdash` | **PartOf** | restarting powerdash restarts the tunnel |
| `lumen-plug-{baseline,digest,findings}` → `lumen-powerdash-vmtunnel` | Wants | timer runs pull the tunnel up if down |
| `lumen-self-healer` → `lumen-messenger` | Wants | healer start pulls messenger |
| `lumen-memoryd` → `ollama` | Wants | memoryd start pulls ollama (embeds are out-of-process HTTP) |
| `lumen-ecc-watch`, `lumen-guardian` → `rasdaemon` | Wants | ECC history source |
| `litellm-auth-alert` → `lumen-litellm-auth-monitor` | Wants | alerter pulls the log producer |
| `lumen-intel`, `lumen-maint-resume` → `docker` | Wants | |
| `lumen-pokemon-live` → `lumen-kiosk-idle` | **OnFailure** | live crash → idle page takes :8871 (wq#1021 preemption pair) |
| 10 units → `ntfy-onfailure@%n` | **OnFailure** | urgent ntfy + aq row: dispatch-lint, fleet-cerebras/mistral/openrouter, foreman-gate@nightly/window, messenger, pat-scan, retirement-audit, scribe |
| `minerva-classifier → proposal → eval → brief` | Before/After | ordering only; each has its own timer slot (xx:30 / xx:45 / +15 / 13:00) |

**Dangling targets (unit does not exist):** `litellm.service` (in After/Wants of the 3 fleet researchers + `lumen-convo-learn`), `lumen-pgvector.service` (After of `lumen-research-index`). Both are docker containers. `Wants=litellm.service` on the researchers is a no-op; their real gate is the `docker.service` Requires plus the container's own `restart=unless-stopped`.

**OnFailure coverage is 10 of 179 services.** Every other failure is caught only by the edge-triggered `lumen-failed-units` sweep (5 min) — which is why that sweep is load-bearing (§3.1).

### 1.3 Port / file edges (the graph systemd does not see)

| provider | consumers (a stop here silently breaks these) |
|---|---|
| **litellm :4000** (docker) | 3 fleet researchers, minerva-classifier/proposal/eval/factory, lumen-model-watchdog, litellm-budget-alert, model-audit, dgm, skillhex, reflector, convo-learn, sentinel probe-synthetic-llm, langfuse-evaluator, LPP escalate paths, messenger `[m:]` picker; auth-monitor tails its logs |
| **ntfy :8080** (docker) | ntfy-send (→ every guard), systemd-onfailure-ntfy, mcp-pressure-guard (direct curl), litellm-budget-alert (direct), lumen-sentinel-watch (direct), n8n-watcher (direct, wrong topic), rosco (direct), guardian, lumen-command-receiver (subscribe), lumen-alert-queue (subscribe), deadman beat (health proof) |
| **lumen-alert-queue → alerts.db** | aq CLI writers (≈20 guards), aq_escalate, aq_autoack, load-watch dedup, alert-hygiene-audit, mission-control/dashboards |
| **queue.db (wq)** | wq CLI (every session + fworker), foreman-dispatch, self_heal, dispatch_health, liveness-clear, tickle, repo_sweep, ui-drift, guardian fire-tickets, aq_escalate, lpp-stuck-supervisor, memoryd (lane identity) |
| **minerva.db** | memoryd, lumen_episode/lumen_state (every session), classifier/proposal/eval, embed-guard, dream/reflector, recall-bench, familiarity, bc250-watch, worklog, telemetry, tag-sections path |
| **ollama :11434** | memoryd embeds, embed-guard (After=ollama), research-index, tag-sections, rerank? (own venv), pelican-bench, qwen36-local rung via LiteLLM |
| **lumen-memoryd :8139** | lumen_agent_v4 lanes (fail-open to legacy router), healer port check |
| **lumen-messenger :8765** | self-healer port check, status-monitor, convo-learn (reads chat DB), public via cloudflared |
| **cloudflared** (one tunnel) | every `*.lumennode.uk`: vault (origin sobek:8222), claude/chat/mctl/scribe/propose/bench/ask/law/pokemon/glance/…; wadjet-ha-monitor **restarts it** on failover (it is also the pipe Commander rides — the documented SIGKILL-mid-script hazard) |
| **desktop-commander-mcp :3100** | claude.ai browser sessions (this audit), mcp-pressure-guard restarts it, node-killer reaps its children, dc-config-guard/dc-patch-tripwire protect its config/patches |
| **sentinel-gatus :8418** (docker) | model-watchdog + sentinel-probes push external endpoints; lumen-sentinel-watch probes it; gatus-ntfy-relay receives its alerts |
| **/home/lumen/logs/.stamp.*** | lumen-cron-deadman (11 units opted in: log-watch, foreman-dispatch, foreman-gate@nightly, foreman-health, foreman-selfheal, liveness-clear, public-guardian, read-dir-sweep, secret-check, session-handoff, wq-health) |
| **Bes** (VictoriaMetrics/VictoriaLogs/HA/MQTT) | vlogs-forwarder (60 s), lumen-powerdash-vmtunnel → plug-baseline/digest/findings, plug-snapshot (Tapo direct), HA-dependent plug control |
| **Thoth** | thoth-origin (autossh → Plex/sonarr/radarr/prowlarr/8080), backup-offsite (restic copy) |
| **Ma'at / Sobek / Bes / Wadjet** (vault PG) | wadjet-ha-monitor (30 s probe), vw-bes-datasync, vw-sobek-datasync, backup-critical (Vaultwarden dump) |

## 2. Unit catalogue (generated 2026-09-10 from `systemctl show`; 300 fleet-authored units under /etc/systemd/system)

Legend: **state** = ActiveState/SubState at capture. `deps` = declared Requires/BindsTo/PartOf/Wants/After (distro targets elided). `dependents` = fleet units that declare a dependency on this one. `alert` = OnFailure hook if any. Timers: cadence from the unit file.

### 2.1 Foundation / platform (15 services, 0 timers, 0 paths)

| unit | state | what / ExecStart | type/restart | cadence (trigger) | deps | dependents | alert |
|---|---|---|---|---|---|---|---|
| `assemble-md0-data.service` | active/exited | Assemble md0 RAID5 /data/lumen fully at boot (all members, safe re-add) — wq#421<br>`/usr/local/sbin/assemble-md0-data.sh` | oneshot/no | boot-oneshot | — | — | — |
| `assemble-md1-scratch.service` | active/exited | Assemble md1 RAID0 scratch array<br>`/usr/local/sbin/assemble-md1-scratch.sh` | oneshot/no | boot-oneshot | — | — | — |
| `cloudflared.service` | active/running | cloudflared<br>`/usr/bin/cloudflared --no-autoupdate --config /etc/cloudflared/config.yml tunnel run` | notify/on-failure | daemon | — | wadjet-ha-monitor.service(After) | — |
| `desktop-commander-mcp.service` | active/running | Desktop Commander MCP via Supergateway<br>`/home/lumen/.npm-global/bin/supergateway --stdio /home/lumen/.npm-global/bin/desktop-commander --port 3100 --outputTrans` | simple/always | daemon | — | lumen-maint-resume.service(After) | — |
| `hugepages-always.service` | active/exited | Set transparent hugepages to always<br>`/bin/bash -c echo always > /sys/kernel/mm/transparent_hugepage/enabled` | oneshot/no | boot-oneshot | — | — | — |
| `iptables-restore.service` | active/exited | Restore iptables rules<br>`/sbin/iptables-restore /etc/iptables/rules.v4` | oneshot/no | boot-oneshot | — | — | — |
| `lumen-maint-resume.service` | inactive/dead | Lumen maintenance window - flip cross-session broadcast to RESUME<br>`/home/lumen/maint/lumen-maint-resume.sh` | oneshot/no | boot-oneshot | Wants=docker.service After=desktop-commander-mcp.service,docker.service | — | — |
| `lumen-power.service` | active/exited | Lumen power tuning (CPU schedutil + AMD GPU low)<br>`/bin/bash -c echo schedutil \| tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor >/dev/null` | oneshot/no | boot-oneshot | — | — | — |
| `mcp-gateway.service` | active/running | Lumen MCP Gateway<br>`/usr/bin/python3 /home/lumen/agent/mcp_gateway.py` | simple/always | daemon | — | — | — |
| `memory-mcp.service` | active/running | Knowledge-Graph Memory MCP (official, via supergateway on 127.0.0.1:3104)<br>`/home/lumen/.npm-global/bin/supergateway --stdio /home/lumen/.npm-global/bin/mcp-server-memory --port 3104 --outputTrans` | simple/always (NRestarts=1) | daemon | — | — | — |
| `nic-park.service` | active/exited | Park unused network ports (power saving)<br>`/usr/local/sbin/nic-park` | oneshot/no | boot-oneshot | — | — | — |
| `ollama.service` | active/running | Ollama Service<br>`/usr/local/bin/ollama serve` | simple/always | daemon | — | lumen-embed-guard.service(After), lumen-memoryd.service(Wants) | — |
| `storage-boot-assert.service` | active/exited | Storage boot assert — assemble/re-add declared arrays + required mounts vs manifest (wq#421)<br>`/usr/bin/python3 /home/lumen/agents/storage-audit/storage_probe.py --boot` | oneshot/no | boot-oneshot | — | — | — |
| `thoth-origin.service` | active/running | Thoth services origin bridge for cloudflared (Lumen->Thoth localhost)<br>`/usr/bin/autossh -M 0 -N -i /home/lumen/.ssh/id_ed25519 -o StrictHostKeyChecking=no -o ServerAliveInterval=15 -o ServerA` | simple/always | daemon | — | — | — |
| `thp-always.service` | active/exited | Enable Transparent HugePages<br>`/bin/sh -c echo always > /sys/kernel/mm/transparent_hugepage/enabled` | oneshot/no | boot-oneshot | — | — | — |

### 2.2 Vault HA + backups (8 services, 8 timers, 0 paths)

| unit | state | what / ExecStart | type/restart | cadence (trigger) | deps | dependents | alert |
|---|---|---|---|---|---|---|---|
| `lumen-backup-critical.service` | inactive/dead | Lumen hourly critical backup (Vaultwarden, Langfuse PG, pgvector, Redis, agent SQLite)<br>`/home/lumen/services/backup/scripts/backup-critical.sh` | oneshot/no | OnCalendar=hourly (lumen-backup-critical.timer) | After=docker.service | — | — |
| `lumen-backup-important.service` | inactive/dead | Lumen daily important backup (Langfuse CH, MinIO, Plausible, MongoDB)<br>`/home/lumen/services/backup/scripts/backup-important.sh` | oneshot/no | OnCalendar=*-*-* 11:00:00 (lumen-backup-important.timer) | After=docker.service | — | — |
| `lumen-backup-offsite.service` | inactive/dead | Lumen offsite restic copy to Thoth<br>`/home/lumen/services/backup/scripts/backup-offsite.sh` | oneshot/no | OnCalendar=*-*-* 12:30:00 UTC (lumen-backup-offsite.timer) | — | — | — |
| `lumen-backup-prune.service` | inactive/dead | Lumen weekly backup retention prune<br>`/home/lumen/services/backup/scripts/backup-prune.sh` | oneshot/no | OnCalendar=Sun *-*-* 04:00:00 (lumen-backup-prune.timer) | — | — | — |
| `lumen-backup-verify.service` | inactive/dead | Lumen monthly backup integrity check (restic check 10% sample)<br>`/home/lumen/services/backup/scripts/backup-verify.sh` | oneshot/no | OnCalendar=*-*-01 09:00:00 (lumen-backup-verify.timer) | — | — | — |
| `vw-bes-datasync.service` | inactive/dead | Vaultwarden data-dir sync Maat->Bes (wq#324 promote payload)<br>`/home/lumen/bin/vw-bes-datasync.sh` | oneshot/no | OnCalendar=hourly (vw-bes-datasync.timer) | — | — | — |
| `vw-sobek-datasync.service` | inactive/dead | Vaultwarden data-dir sync Maat->Sobek (wq#414 promote payload)<br>`/home/lumen/bin/vw-sobek-datasync.sh` | oneshot/no | OnCalendar=hourly (vw-sobek-datasync.timer) | — | — | — |
| `wadjet-ha-monitor.service` | inactive/dead | Wadjet HA vault auto-promote monitor (probe Ma'at, failover to Wadjet)<br>`/home/lumen/bin/wadjet-ha-monitor.sh` | oneshot/no | OnUnitActiveSec=30; OnBootSec=90 (wadjet-ha-monitor.timer) | After=cloudflared.service | — | — |

### 2.3 Alert path + triage (aq/ntfy) (18 services, 5 timers, 0 paths)

| unit | state | what / ExecStart | type/restart | cadence (trigger) | deps | dependents | alert |
|---|---|---|---|---|---|---|---|
| `alert-deadman-beat.service` | inactive/dead | Beat the off-fleet alert-path dead man's switch (wq#1039)<br>`/home/lumen/bin/alert-deadman-beat.sh` | oneshot/no | OnUnitActiveSec=5min; OnBootSec=2min (alert-deadman-beat.timer) | — | — | — |
| `deadman-killtest.service` | inactive/dead | Quarterly kill-test of the off-fleet alert-path dead man's switch (wq#1039)<br>`/home/lumen/bin/deadman-killtest.sh` | oneshot/no | OnCalendar=*-01,04,07,10-01 09:00:00 (deadman-killtest.timer) | — | — | — |
| `lumen-alert-hygiene-audit.service` | inactive/dead | Standing quarterly alert-hygiene audit (wq#1042, R5 G4) — 90-day rule + page-field audit<br>`/usr/bin/python3 /home/lumen/foreman/ops/alert_hygiene_audit.py` | oneshot/no | OnCalendar=*-01,04,07,10-01 08:00:00 UTC (lumen-alert-hygiene-audit.timer) | — | — | — |
| `lumen-alert-queue.service` | active/running | Lumen Alert Queue — persistent ntfy alert subscriber (lumen-alerts,lumen-updates -> SQLite triage)<br>`/usr/bin/python3 /home/lumen/agents/alert-queue/ntfy_alert_subscriber.py` | notify/always | daemon | After=docker.service | — | — |
| `lumen-aq-autoack.service` | inactive/dead | aq auto-ack stale info-class alerts<br>`/usr/bin/python3 /home/lumen/agents/alert-queue/aq_autoack.py` | oneshot/no | OnCalendar=*-*-* 09:00:00 UTC (lumen-aq-autoack.timer) | — | — | — |
| `lumen-aq-escalate.service` | inactive/dead | aq escalation responder — unacked CRITICAL probe repeats -> fleet-fires ticket + one page (wq#965)<br>`/usr/bin/python3 /home/lumen/bin/aq_escalate.py` | oneshot/no | OnUnitActiveSec=10min; OnBootSec=5min (lumen-aq-escalate.timer) | — | — | — |
| `lumen-command-receiver.service` | active/running | Lumen ntfy Command Receiver — phone control<br>`/usr/bin/python3 /home/lumen/agents/command-receiver/ntfy_command_receiver.py` | notify/always | daemon | — | — | — |
| `lumen-gatus-ntfy-relay.service` | active/running | Gatus-to-ntfy-send alert relay (wq#1184)<br>`/usr/bin/python3 /home/lumen/bin/gatus-ntfy-relay` | simple/on-failure | daemon | — | — | — |
| `ntfy-onfailure@lumen-dispatch-lint.service.service` | inactive/dead | ntfy alert on failure of lumen-dispatch-lint.service (wq#392)<br>`/home/lumen/bin/systemd-onfailure-ntfy lumen-dispatch-lint.service` | oneshot/no | boot-oneshot | — | — | — |
| `ntfy-onfailure@lumen-fleet-cerebras.service` | inactive/dead | ntfy alert on failure of lumen-fleet-cerebras (wq#392)<br>`/home/lumen/bin/systemd-onfailure-ntfy lumen-fleet-cerebras` | oneshot/no | boot-oneshot | — | — | — |
| `ntfy-onfailure@lumen-fleet-mistral.service` | inactive/dead | ntfy alert on failure of lumen-fleet-mistral (wq#392)<br>`/home/lumen/bin/systemd-onfailure-ntfy lumen-fleet-mistral` | oneshot/no | boot-oneshot | — | — | — |
| `ntfy-onfailure@lumen-fleet-openrouter.service` | inactive/dead | ntfy alert on failure of lumen-fleet-openrouter (wq#392)<br>`/home/lumen/bin/systemd-onfailure-ntfy lumen-fleet-openrouter` | oneshot/no | boot-oneshot | — | — | — |
| `ntfy-onfailure@lumen-foreman-gate@nightly.service.service` | inactive/dead | ntfy alert on failure of lumen-foreman-gate@nightly.service (wq#392)<br>`/home/lumen/bin/systemd-onfailure-ntfy lumen-foreman-gate@nightly.service` | oneshot/no | boot-oneshot | — | — | — |
| `ntfy-onfailure@lumen-foreman-gate@window.service.service` | inactive/dead | ntfy alert on failure of lumen-foreman-gate@window.service (wq#392)<br>`/home/lumen/bin/systemd-onfailure-ntfy lumen-foreman-gate@window.service` | oneshot/no | boot-oneshot | — | — | — |
| `ntfy-onfailure@lumen-messenger.service` | inactive/dead | ntfy alert on failure of lumen-messenger (wq#392)<br>`/home/lumen/bin/systemd-onfailure-ntfy lumen-messenger` | oneshot/no | boot-oneshot | — | — | — |
| `ntfy-onfailure@lumen-pat-scan.service.service` | inactive/dead | ntfy alert on failure of lumen-pat-scan.service (wq#392)<br>`/home/lumen/bin/systemd-onfailure-ntfy lumen-pat-scan.service` | oneshot/no | boot-oneshot | — | — | — |
| `ntfy-onfailure@lumen-retirement-audit.service.service` | inactive/dead | ntfy alert on failure of lumen-retirement-audit.service (wq#392)<br>`/home/lumen/bin/systemd-onfailure-ntfy lumen-retirement-audit.service` | oneshot/no | boot-oneshot | — | — | — |
| `ntfy-onfailure@lumen-scribe.service` | inactive/dead | ntfy alert on failure of lumen-scribe (wq#392)<br>`/home/lumen/bin/systemd-onfailure-ntfy lumen-scribe` | oneshot/no | boot-oneshot | — | — | — |

### 2.4 Guards / watchdogs / sentinels (39 services, 32 timers, 0 paths)

| unit | state | what / ExecStart | type/restart | cadence (trigger) | deps | dependents | alert |
|---|---|---|---|---|---|---|---|
| `dispatch-watchdog.service` | inactive/dead | Foreman dispatch watchdog — detect stall, auto-recover, page<br>`/usr/bin/python3 /home/lumen/bin/dispatch-watchdog.py` | oneshot/no | OnUnitActiveSec=4min; OnBootSec=2min (dispatch-watchdog.timer) | — | — | — |
| `fleet-auth-ssh-probe.service` | inactive/dead | Fleet synthetic AUTH-layer SSH probe (closes monitoring_gap; fail-loud to ntfy.sh)<br>`/home/lumen/bin/fleet-auth-ssh-probe.sh` | oneshot/no | OnUnitActiveSec=5min; OnBootSec=3min (fleet-auth-ssh-probe.timer) | After=tailscaled.service | — | — |
| `gatus-sshauth-probe.service` | inactive/dead | Gatus push-model SSH-AUTH probe (t85; restricted key -> Gatus external endpoints)<br>`/home/lumen/services/sentinel/gatus-sshauth-probe.sh` | oneshot/no | OnUnitActiveSec=5min; OnBootSec=4min (gatus-sshauth-probe.timer) | After=tailscaled.service | — | — |
| `langfuse-idle-alert.service` | inactive/dead | Lumen Langfuse idle alert (6h zero-traces ntfy push)<br>`/usr/bin/python3 /home/lumen/services/langfuse-ops/idle_alert.py --window-hours 6` | oneshot/no | OnUnitActiveSec=1h; OnBootSec=10min (langfuse-idle-alert.timer) | After=docker.service | — | — |
| `litellm-auth-alert.service` | inactive/dead | LiteLLM auth-failure burst alerter (wq#911, consumes auth-failures.jsonl)<br>`/home/lumen/bin/litellm-auth-alert` | oneshot/no | OnUnitActiveSec=2min; OnBootSec=3min (litellm-auth-alert.timer) | Wants=lumen-litellm-auth-monitor.service | — | — |
| `lumen-bc250-watch.service` | inactive/dead | BC-250 intel tripwire — keyword match in minerva.db -> ntfy lumen-alerts, bypasses self_relevance gate<br>`/home/lumen/bin/bc250-watch` | oneshot/no | OnUnitActiveSec=15min; OnBootSec=5min (lumen-bc250-watch.timer) | — | — | — |
| `lumen-cron-deadman.service` | inactive/dead | Lumen cron dead-man's switch — stamp freshness check (wq#1184)<br>`/usr/bin/python3 /home/lumen/foreman/ops/wq1184-gatus-health-checks/cron_deadman.py check` | oneshot/no | OnUnitActiveSec=10min; OnBootSec=5min (lumen-cron-deadman.timer) | — | — | — |
| `lumen-ecc-rollup.service` | inactive/dead | Lumen daily ECC health rollup to ntfy<br>`/home/lumen/bin/lumen-ecc-rollup` | oneshot/no | OnCalendar=*-*-* 15:05:00 UTC (lumen-ecc-rollup.timer) | — | — | — |
| `lumen-ecc-watch.service` | inactive/dead | Lumen EDAC ECC error delta watcher (ntfy on new CE/UE)<br>`/home/lumen/bin/lumen-ecc-watch` | oneshot/no | OnUnitActiveSec=5min; OnBootSec=3min (lumen-ecc-watch.timer) | Wants=rasdaemon.service After=rasdaemon.service | — | — |
| `lumen-embed-guard.service` | inactive/dead | Memory embedding store integrity guard + repair (wq#465)<br>`/usr/bin/python3 /home/lumen/minerva/memory_embed_guard.py` | oneshot/no | OnCalendar=hourly (lumen-embed-guard.timer) | After=ollama.service | — | — |
| `lumen-failed-units.service` | inactive/dead | Lumen — edge-triggered sweep for failed systemd units<br>`/home/lumen/bin/lumen-failed-units` | oneshot/no | OnUnitActiveSec=5min; OnBootSec=3min (lumen-failed-units.timer) | — | — | — |
| `lumen-foreman-gate@nightly.service` | inactive/dead | Lumen foreman dispatch gate (nightly) — prove the next pass can dispatch (wq#1411)<br>`/usr/bin/python3 /home/lumen/foreman/dispatcher/dispatch_recover.py gate --mode nightly` | oneshot/no | OnCalendar=*-*-* 22:00 America/Los_Angeles (lumen-foreman-nightly-gate.timer) | — | — | ntfy-onfailure@lumen-foreman-gate@nightly.service.service |
| `lumen-foreman-gate@window.service` | inactive/dead | Lumen foreman dispatch gate (window) — prove the next pass can dispatch (wq#1411)<br>`/usr/bin/python3 /home/lumen/foreman/dispatcher/dispatch_recover.py gate --mode window` | oneshot/no | OnCalendar=Thu 00:30 America/Los_Angeles (lumen-foreman-window-gate.timer) | — | — | ntfy-onfailure@lumen-foreman-gate@window.service.service |
| `lumen-foreman-health-window.service` | inactive/dead | Lumen dispatcher health check, Thu window: K=1, 20-min dedup (wq#1411)<br>`/usr/bin/python3 /home/lumen/foreman/dispatcher/dispatch_health.py check --threshold 1 --dedup 1200 --no-ladder` | oneshot/no | OnCalendar=Thu *-*-* 01..08:00/5 America/Los_Angeles (lumen-foreman-health-window.timer) | — | — | — |
| `lumen-foreman-health.service` | inactive/dead | Lumen dispatcher health check — alarm on consecutive stalled passes (wq#825)<br>`/usr/bin/python3 /home/lumen/foreman/dispatcher/dispatch_health.py check` | oneshot/no | OnUnitActiveSec=5min; OnBootSec=3min (lumen-foreman-health.timer) | — | — | — |
| `lumen-foreman-selfheal.service` | inactive/dead | Lumen wq self-heal — release INFRA_CRASH blocks, leave WORK_BLOCKED/LIMIT_HIT (wq#725)<br>`/usr/bin/python3 /home/lumen/foreman/dispatcher/self_heal.py run --once` | oneshot/no | OnUnitActiveSec=10min; OnBootSec=5min (lumen-foreman-selfheal.timer) | — | — | — |
| `lumen-guardian.service` | active/running | Lumen Guardian — deterministic hardware-threshold shutdown daemon<br>`/usr/bin/python3 /home/lumen/bin/lumen-guardian.py` | simple/always | daemon | Wants=rasdaemon.service After=rasdaemon.service | — | — |
| `lumen-kiosk-idle.service` | **failed/failed** | LPP Kiosk Idle Page (serves /home/lumen/pokemon/assets on :8871)<br>`/usr/bin/python3 -m http.server 8871 --bind 0.0.0.0` | simple/always | — | — | — | — |
| `lumen-litellm-auth-monitor.service` | active/running | LiteLLM auth failure monitor (wq#721)<br>`/home/lumen/bin/litellm-auth-monitor` | simple/always (NRestarts=8) | daemon | Requires=docker.service After=docker.service | litellm-auth-alert.service(Wants) | — |
| `lumen-liveness-clear.service` | inactive/dead | wq#1478 liveness clear — auto-close Frank-gated tickets whose premise is met on live state (LIVENESS_GATE.md §12.3)<br>`/usr/bin/python3 /home/lumen/foreman/harness/liveness_clear.py --apply --write-bindings` | oneshot/no | OnCalendar=*:07,37 (lumen-liveness-clear.timer) | — | — | — |
| `lumen-load-watch.service` | inactive/dead | Lumen relative load watch (load5/nproc, sustained, edge-triggered) — wq#1487<br>`/usr/bin/python3 /home/lumen/bin/lumen-load-watch` | oneshot/no | OnUnitActiveSec=1min; OnBootSec=2min (lumen-load-watch.timer) | — | — | — |
| `lumen-lpp-stuck-supervisor.service` | inactive/dead | LPP stuck-supervisor — story_score/MODE stall detect + bounded intervene + auto-ticket/escalate (wq#671-673)<br>`/home/lumen/pokemon/tools/lpp-stuck-supervisor --quiet` | oneshot/no | OnUnitActiveSec=30min; OnBootSec=5min (lumen-lpp-stuck-supervisor.timer) | — | — | — |
| `lumen-model-watchdog.service` | inactive/dead | Lumen model endpoint watchdog (LiteLLM rungs incl local fallback) — wq#90<br>`/usr/bin/python3 /home/lumen/agents/model-watchdog/lumen_model_watchdog.py` | oneshot/no | OnUnitActiveSec=10min; OnBootSec=3min (lumen-model-watchdog.timer) | — | — | — |
| `lumen-monitor.service` | active/running | Lumen Status Monitor<br>`/usr/bin/python3 /home/lumen/agent/lumen_status_monitor.py` | simple/always | daemon | — | lumen-dashboard.service(After) | — |
| `lumen-node-killer.service` | active/running | Lumen Node Process Killer — frees RAM from zombie Desktop Commander workers<br>`/usr/bin/python3 /home/lumen/projects/03045bd4/node_killer.py` | simple/always | daemon | — | — | — |
| `lumen-public-guardian.service` | inactive/dead | Lumen public-surface guardian — email + LPP stream (wq#1252)<br>`/usr/bin/python3 /home/lumen/services/lumen-public-guardian/guardian.py run` | oneshot/no | OnUnitActiveSec=2min; OnBootSec=2min (lumen-public-guardian.timer) | — | — | — |
| `lumen-rerank-thread-watch.service` | inactive/dead | wq#596 rerank 16-thread watch + auto-rollback (48h window)<br>`/home/lumen/bin/rerank-thread-watch.sh` | oneshot/no | OnUnitActiveSec=5min; OnBootSec=2min (lumen-rerank-thread-watch.timer) | — | — | — |
| `lumen-retirement-audit.service` | inactive/dead | retirement-audit — weekly sweep for retired services still running, still enabled, or still burning RAM (wq#723)<br>`/home/lumen/bin/retirement-audit run` | oneshot/no | OnCalendar=Mon *-*-* 09:17:00 UTC (lumen-retirement-audit.timer) | After=docker.service | — | ntfy-onfailure@lumen-retirement-audit.service.service |
| `lumen-rosco.service` | inactive/dead | Rosco RAM Janitor — kills stale processes only under RAM pressure<br>`/usr/bin/python3 /home/lumen/agent/rosco.py` | oneshot/no | OnUnitActiveSec=1h; OnBootSec=15min (lumen-rosco.timer) | — | — | — |
| `lumen-self-healer.service` | active/running | Lumen Self-Healing Service Monitor<br>`/usr/bin/python3 /home/lumen/agent/lumen_self_healer.py` | notify/always | daemon | Wants=lumen-messenger.service After=lumen-messenger.service | — | — |
| `lumen-sentinel-watch.service` | inactive/dead | Reciprocal watchdog — Lumen checks the Sentinel is alive<br>`/bin/bash -c curl -sf -m 10 http://localhost:8418/api/v1/endpoints/statuses > /dev/null \|\| curl -s -m 5 -d "Sentinel (` | oneshot/no | OnUnitActiveSec=5min; OnBootSec=3min (lumen-sentinel-watch.timer) | — | — | — |
| `lumen-split-watch.service` | inactive/dead | agent5 split idle/error watch (wq#140, edge-triggered -> ntfy lumen-alerts)<br>`/usr/bin/python3 /home/lumen/agents/split-watch/split_watch.py` | oneshot/no | OnUnitActiveSec=10min; OnBootSec=5min (lumen-split-watch.timer) | — | — | — |
| `lumen-sqlite-health.service` | inactive/dead | Weekly read-only SQLite health check across Lumen archival stores (wq#91)<br>`/home/lumen/bin/lumen-sqlutil health --all --alert` | oneshot/no | OnCalendar=Sun 10:00 UTC (lumen-sqlite-health.timer) | — | — | — |
| `lumen-state-reconcile.service` | inactive/dead | Reconcile bi-temporal fleet-state store against live tailscale (Memory v2 P1 change-time hook)<br>`/usr/bin/python3 /home/lumen/minerva/state_reconcile.py` | oneshot/no | OnCalendar=*-*-* 17:30:00 UTC (lumen-state-reconcile.timer) | After=tailscaled.service | — | — |
| `lumen-tickle.service` | inactive/dead | Lumen tickler — fire due deferred-work items into wq<br>`/usr/bin/python3 /home/lumen/services/lumen-tickle/tickle.py fire` | oneshot/no | OnCalendar=hourly; OnBootSec=10min (lumen-tickle.timer) | — | — | — |
| `mcp-pressure-guard.service` | inactive/dead | MCP gateway pressure guard (wq#380 auto-recovery)<br>`/home/lumen/bin/mcp-pressure-guard.sh` | oneshot/no | OnUnitActiveSec=60; OnBootSec=2min (mcp-pressure-guard.timer) | — | — | — |
| `n8n-watcher.service` | active/running | n8n Error Watcher — ntfy alerting for workflow failures<br>`/usr/bin/python3 /home/lumen/agent/n8n_watcher.py` | simple/always | daemon | Wants=n8n.service After=n8n.service | — | — |
| `power-remediate.service` | inactive/dead | Power-remediate — unreachable-node auto-remediation (STAGED — ARMED=0 in conf until Frank flips it)<br>`/usr/bin/python3 /home/lumen/bin/power-remediate run` | oneshot/no | OnUnitActiveSec=60s; OnBootSec=60s (power-remediate.timer) | — | — | — |
| `sentinel-probes.service` | inactive/dead | Fleet Sentinel — Tier 2 function-correctness probes<br>`/usr/bin/python3 /home/lumen/sentinel/probes/sentinel_probes.py` | oneshot/no | OnUnitActiveSec=30min; OnBootSec=5min (sentinel-probes.timer) | — | — | — |

### 2.5 Drift / integrity guards (git, units, config, secrets) (20 services, 20 timers, 1 paths)

| unit | state | what / ExecStart | type/restart | cadence (trigger) | deps | dependents | alert |
|---|---|---|---|---|---|---|---|
| `commit-hygiene-sweep.service` | inactive/dead | Commit-hygiene sweep — flag dirty/unpushed/behind fleet git trees (wq: git hygiene)<br>`/home/lumen/bin/git-hygiene-sweep` | oneshot/no | OnCalendar=*-*-* 09:00:00 (commit-hygiene-sweep.timer) | — | — | — |
| `dc-config-guard.service` | inactive/dead | Re-enforce tier-1 Desktop Commander config on drift<br>`/usr/local/sbin/dc-config-guard.sh` | oneshot/no | OnUnitActiveSec=5min; OnBootSec=3min (dc-config-guard.timer); path (dc-config-guard.path) | — | — | — |
| `dc-patch-tripwire.service` | inactive/dead | DC patch tripwire — detect silent desktop-commander upgrades wiping Lumen patches (wq#416)<br>`/home/lumen/bin/dc-patch-tripwire` | oneshot/no | OnCalendar=*-*-* 09:00:00 UTC (dc-patch-tripwire.timer) | — | — | — |
| `drift-assert.service` | inactive/dead | drift-assert v2 — nightly config drift vs golden facts, proposal-only (wq#393)<br>`/home/lumen/bin/drift-assert` | oneshot/no | OnCalendar=*-*-* 08:30:00 UTC (drift-assert.timer) | — | — | — |
| `git-guard-sentinel.service` | inactive/dead | git-guard-sentinel — alert when lumen-git-guard is bypassed on a Class A default branch<br>`/usr/bin/python3 /home/lumen/agents/git-guard-sentinel/git_guard_sentinel.py` | oneshot/no | OnUnitActiveSec=1h; OnBootSec=5min (git-guard-sentinel.timer) | — | — | — |
| `gitea-version-check.service` | inactive/dead | wq#1397 — check live Gitea version against CVE-2026-66877 patched floor<br>`/usr/bin/python3 /home/lumen/services/gitea-version-check/gitea_version_check.py run` | oneshot/no | OnUnitActiveSec=24h; OnBootSec=10min (gitea-version-check.timer) | — | — | — |
| `lumen-disk-hygiene.service` | inactive/dead | Lumen conservative disk hygiene (dead-space reclamation)<br>`/home/lumen/bin/lumen-disk-hygiene` | oneshot/no | OnCalendar=*-*-* 11:30:00 UTC (lumen-disk-hygiene.timer) | After=docker.service | — | — |
| `lumen-dispatch-lint.service` | inactive/dead | dispatch-lint — alert when a dispatch file still instructs a retired rule (wq#533/wq#565)<br>`/home/lumen/bin/dispatch-lint` | oneshot/no | OnCalendar=daily (lumen-dispatch-lint.timer) | — | — | ntfy-onfailure@lumen-dispatch-lint.service.service |
| `lumen-memory-audit.service` | inactive/dead | pgvector embedding integrity/version-drift audit (prop 0658b2f0f457, wq#452)<br>`/usr/bin/python3 /home/lumen/minerva/memory_audit.py` | oneshot/no | OnCalendar=Tue *-*-* 09:10:00 UTC (lumen-memory-audit.timer) | — | — | — |
| `lumen-model-audit.service` | inactive/dead | Known-answer correctness audit of external LiteLLM rungs (prop c1fdd68427b6, wq#449)<br>`/usr/bin/python3 /home/lumen/minerva/model_audit.py` | oneshot/no | OnCalendar=*-*-* 03,15:40:00 UTC (lumen-model-audit.timer) | — | — | — |
| `lumen-pat-scan.service` | **failed/failed** | pat-scan — alert when a GitHub token literal is world-readable or still live (wq#523/wq#565)<br>`/home/lumen/bin/wq523_pat_residue.py --alert` | oneshot/no | OnCalendar=daily (lumen-pat-scan.timer) | — | — | ntfy-onfailure@lumen-pat-scan.service.service |
| `lumen-read-dir-sweep.service` | inactive/dead | wq#1482 read_dir_sweep — masked secret sweep of every allow.read_dirs root, written to harness/runtime/read_dir_sweep.json; capability_lint refuses a fresh hit and fails closed on a stale/missing sweep<br>`/usr/bin/python3 /home/lumen/foreman/harness/read_dir_sweep.py --write-runtime` | oneshot/no | OnCalendar=*-*-* 05:15:00 (lumen-read-dir-sweep.timer) | — | — | — |
| `lumen-repo-sweep.service` | inactive/dead | wq#800 managed-repo divergence sweep<br>`/usr/bin/python3 /home/lumen/foreman/harness/repo_sweep.py` | oneshot/no | OnCalendar=*-*-* 02,06,10,14,18,22:15:00 (lumen-repo-sweep.timer) | — | — | — |
| `lumen-scratch-sweep.service` | inactive/dead | Lumen scratch retention sweep (dry-run logging only)<br>`/usr/bin/python3 /home/lumen/minerva/retention/scratch_sweep.py` | oneshot/no | OnCalendar=Sun *-*-* 04:00:00 UTC (lumen-scratch-sweep.timer) | — | — | — |
| `lumen-trivy-weekly.service` | inactive/dead | wq#858 Trivy weekly CVE scan — containers + host rootfs<br>`/home/lumen/services/trivy-weekly/scan.sh` | oneshot/no | OnCalendar=Sun *-*-* 09:00:00 (lumen-trivy-weekly.timer) | After=docker.service | — | — |
| `lumen-ui-drift.service` | inactive/dead | ui-drift (wq#770) — dashboards-vs-reality probe (read-only, reports + tickets only)<br>`/home/lumen/bin/ui-drift` | oneshot/no | OnCalendar=*-*-* 11:15:00 (lumen-ui-drift.timer) | After=docker.service | — | — |
| `primary-tree-guard.service` | **failed/failed** | primary tree guard — assert PRIMARY trees are on their default branch, alert+restore on drift (wq#900)<br>`/usr/bin/python3 /home/lumen/foreman/harness/primary_tree_guard.py check` | oneshot/no | OnUnitActiveSec=5min; OnBootSec=3min (primary-tree-guard.timer) | — | — | — |
| `repo-visibility-sentinel.service` | inactive/dead | repo-visibility-sentinel — alert on any public GitHub repo not on allowlist<br>`/usr/bin/python3 /home/lumen/agents/repo-visibility-sentinel/repo_sentinel.py` | oneshot/no | OnUnitActiveSec=15min; OnBootSec=5min (repo-visibility-sentinel.timer) | — | — | — |
| `unit-drift.service` | inactive/dead | unit-drift (wq#1041) read-only drift check: /etc/systemd/system vs git baseline<br>`/home/lumen/bin/unit-drift check --digest` | oneshot/no | OnUnitActiveSec=15min; OnBootSec=10min (unit-drift.timer) | — | — | — |
| `wq-drift-guard.service` | **failed/failed** | wq drift detection — alert if /home/lumen/bin/wq diverges from git HEAD (wq#886)<br>`/home/lumen/bin/wq-drift-guard` | oneshot/no | OnUnitActiveSec=5min; OnBootSec=2min (wq-drift-guard.timer) | — | — | — |
| `dc-config-guard.path` | active/waiting | Watch DC config.json for clobbers (0.2.46 child onboarding reset) | path | PathChanged: [Unit] | — | dc-config-guard.service(After) | — |

### 2.6 Orchestration (wq/foreman/agent lanes) (20 services, 10 timers, 0 paths)

| unit | state | what / ExecStart | type/restart | cadence (trigger) | deps | dependents | alert |
|---|---|---|---|---|---|---|---|
| `lumen-agent.service` | inactive/dead | lumen-agent.service | ?/no | — | — | lumen-dashboard.service(After) | — |
| `lumen-copilot-status.service` | active/running | Lumen Copilot build-state panel (read-only)<br>`/usr/bin/python3 -m uvicorn app:app --host 0.0.0.0 --port 8145` | simple/always | daemon | — | — | — |
| `lumen-cord.service` | active/running | Cord — Frank->agent secret handoff (127.0.0.1:8152)<br>`/usr/bin/python3 -m uvicorn app:app --host 127.0.0.1 --port 8152 --no-access-log` | simple/always | daemon | — | — | — |
| `lumen-dgm.service` | inactive/dead | Lumen DGM self-improvement cycle (propose, sandbox db6, gate, pending+ntfy; human-gated)<br>`/usr/bin/python3 /home/lumen/autonomy/agent5/dgm.py run` | oneshot/no | OnCalendar=*-*-* 17:00:00 UTC (lumen-dgm.timer) | — | — | — |
| `lumen-fleet-cerebras.service` | active/running | Lumen Fleet Researcher — cerebras<br>`/usr/bin/python3 /home/lumen/agent/lumen_fleet_researcher.py` | notify/always | daemon | Requires=docker.service Wants=litellm.service,docker.service After=docker.service,redis-server.service,litellm.service | — | ntfy-onfailure@lumen-fleet-cerebras.service |
| `lumen-fleet-mistral.service` | active/running | Lumen Fleet Researcher — mistral<br>`/usr/bin/python3 /home/lumen/agent/lumen_fleet_researcher.py` | notify/always | daemon | Requires=docker.service Wants=litellm.service,docker.service After=docker.service,redis-server.service,litellm.service | — | ntfy-onfailure@lumen-fleet-mistral.service |
| `lumen-fleet-openrouter.service` | active/running | Lumen Fleet Researcher — openrouter<br>`/usr/bin/python3 /home/lumen/agent/lumen_fleet_researcher.py` | notify/always | daemon | Requires=docker.service Wants=litellm.service,docker.service After=litellm.service,docker.service,redis-server.service | — | ntfy-onfailure@lumen-fleet-openrouter.service |
| `lumen-foreman-dispatch.service` | inactive/dead | Lumen foreman auto-dispatcher — one scan/dispatch pass (wq#715)<br>`/usr/bin/python3 /home/lumen/foreman/dispatcher/foreman_dispatcher.py run --once` | oneshot/no | OnUnitActiveSec=20min; OnUnitActiveSec=5min; OnBootSec=10min (lumen-foreman-dispatch.timer) | — | — | — |
| `lumen-intel.service` | inactive/dead | Lumen Intel Engine - Academic & Newsletter Intelligence<br>`/usr/bin/python3 /home/lumen/agent/lumen_intel.py` | oneshot/no | OnUnitActiveSec=4h; OnBootSec=5min (lumen-intel.timer) | Wants=docker.service After=docker.service,redis-server.service | — | — |
| `lumen-intervention-ledger.service` | inactive/dead | R4 intervention ledger daily collect+snapshot+emit<br>`/usr/bin/python3 /home/lumen/foreman/ops/intervention_ledger.py run` | oneshot/no | OnCalendar=*-*-* 06:15:00 UTC (lumen-intervention-ledger.timer) | — | — | — |
| `lumen-mission-control.service` | active/running | Lumen Mission Control — wq orchestration UI (mctl.lumennode.uk)<br>`/usr/bin/python3 -m uvicorn app:app --host 127.0.0.1 --port 8136` | simple/always | daemon | — | — | — |
| `lumen-prompt-forge.service` | active/running | Lumen Prompt Forge — fleet-aware prompt generator (wq#957)<br>`/usr/bin/python3 /home/lumen/foreman/prompt-forge/server.py --bind 127.0.0.1 --port 8792` | simple/on-failure | daemon | — | — | — |
| `lumen-proposals-ui.service` | active/running | Minerva Proposals Review UI (propose.lumennode.uk)<br>`/usr/bin/python3 -m uvicorn app:app --host 127.0.0.1 --port 8110` | simple/always | daemon | — | — | — |
| `lumen-scout.service` | inactive/dead | Lumen GitHub Daily Scout — queue gap-analysis for trending repos<br>`/usr/bin/python3 /home/lumen/autonomy/lumen_scout_graph.py` | oneshot/no | OnCalendar=*-*-* 06:00:00 UTC (lumen-scout.timer) | — | — | — |
| `lumen-scribe.service` | active/running | Scribe task tracker (scribe.lumennode.uk)<br>`/usr/bin/python3 -m uvicorn app:app --host 127.0.0.1 --port 8131` | simple/always | daemon | — | — | ntfy-onfailure@lumen-scribe.service |
| `lumen-signal-engine.service` | inactive/dead | Lumen Signal Engine — daily real-time content intelligence<br>`/usr/bin/python3 /home/lumen/agent/signal_engine.py` | oneshot/no | OnCalendar=*-*-* 06:00:00 UTC (lumen-signal-engine.timer) | — | — | — |
| `lumen-skillhex.service` | inactive/dead | SkillHEX hypothesis loop over agent5 (evidence-grounded prompt mutation, human-gated; prop 5ccfeb0adf86)<br>`/usr/bin/python3 /home/lumen/autonomy/agent5/skillhex.py --n 6` | oneshot/no | OnCalendar=Mon,Wed,Fri *-*-* 12:30:00 UTC (lumen-skillhex.timer) | After=redis-server.service | — | — |
| `renovate-gitea.service` | inactive/dead | wq#859 Renovate — scan Gitea (Anubis) repos, open dependency update PRs<br>`/usr/bin/node /home/lumen/.npm-global/lib/node_modules/renovate/dist/renovate.js` | oneshot/no | OnUnitActiveSec=4h; OnBootSec=10min (renovate-gitea.timer) | — | — | — |
| `session-handoff.service` | inactive/dead | session-handoff — regenerate kb/SESSION-HANDOFF.md from live fleet state (wq#1294)<br>`/usr/bin/python3 /home/lumen/bin/session-handoff` | oneshot/no | OnUnitActiveSec=30min; OnBootSec=3min (session-handoff.timer) | — | — | — |
| `sia-news-pull.service` | inactive/dead | Pull scored news prompts from sia into research queue<br>`/home/lumen/bin/sia-news-pull` | oneshot/no | OnUnitActiveSec=2min; OnBootSec=2min (sia-news-pull.timer) | — | — | — |

### 2.7 Minerva memory + pipeline (21 services, 18 timers, 1 paths)

| unit | state | what / ExecStart | type/restart | cadence (trigger) | deps | dependents | alert |
|---|---|---|---|---|---|---|---|
| `langfuse-evaluator-smoke.service` | failed/failed (stale — wq#1648 fixed the cause 09-11; clears on the next timer run) | Lumen Langfuse evaluator judge-path smoke test (wq#989 weekly connectivity check)<br>`/usr/bin/python3 /home/lumen/services/langfuse-ops/evaluator.py --smoke --verbose` | oneshot/no | OnCalendar=Sun 10:50 UTC (langfuse-evaluator-smoke.timer) | After=docker.service | — | — |
| `langfuse-evaluator.service` | inactive/dead | Lumen Langfuse external evaluator (LLM-as-judge, 5% sample)<br>`/usr/bin/python3 /home/lumen/services/langfuse-ops/evaluator.py --window-min 15 --sample-pct 5 --max-evals 20` | oneshot/no | OnUnitActiveSec=15min; OnBootSec=15min (langfuse-evaluator.timer) | After=docker.service | — | — |
| `lumen-convo-learn.service` | inactive/dead | Lumen learns from messenger conversations (extract durable learnings -> episode)<br>`/usr/bin/python3 /home/lumen/minerva/lumen_convo_learn.py` | oneshot/no | OnUnitActiveSec=6h; OnBootSec=20min (lumen-convo-learn.timer) | After=litellm.service | — | — |
| `lumen-dream.service` | inactive/dead | Lumen memory consolidation pass (Memory v2 P3, PROPOSAL-ONLY -- never mutates memory)<br>`/usr/bin/python3 /home/lumen/minerva/lumen_dream.py` | oneshot/no | OnCalendar=Sun *-*-* 18:00:00 UTC (lumen-dream.timer) | — | — | — |
| `lumen-familiarity.service` | inactive/dead | Entity-familiarity ingest from classified_intel (behavioral proxy, arXiv:2607.13568; prop fd2622e69837)<br>`/usr/bin/python3 /home/lumen/minerva/familiarity_scorer.py ingest` | oneshot/no | OnCalendar=*-*-* 01,07,13,19:20:00 UTC (lumen-familiarity.timer) | — | — | — |
| `lumen-judgearena-bench.service` | inactive/dead | JudgeArena judge-agreement bench vs human labels (arXiv:2608.02620, prop 9455507d9aeb)<br>`/home/lumen/judgearena/.venv/bin/python3 /home/lumen/minerva/judgearena_bench.py` | oneshot/no | OnCalendar=Sun *-*-* 06:15:00 UTC (lumen-judgearena-bench.timer) | — | — | — |
| `lumen-memoryd.service` | active/running | lumen-memoryd — guarded memory service for agent lanes (wq#575)<br>`/usr/bin/python3 /home/lumen/minerva/v4/memoryd/daemon.py` | notify/always | daemon | Wants=ollama.service | — | — |
| `lumen-pelican-bench.service` | inactive/dead | Pelican-style probe battery over local models (wq#441, prop d979fd19f54f)<br>`/usr/bin/python3 /home/lumen/minerva/pelican_bench.py` | oneshot/no | OnCalendar=Sun 12:00 UTC (lumen-pelican-bench.timer) | — | — | — |
| `lumen-recall-bench.service` | inactive/dead | wq#572 quiet-hours recall bench + drift alarm (p50+accuracy only)<br>`/home/lumen/minerva/eval/run_sched_bench.sh` | oneshot/no | OnCalendar=*-*-* 10:37:00 (lumen-recall-bench.timer) | — | — | — |
| `lumen-reflector.service` | inactive/dead | Lumen LLM sleep-time reflector (Memory v2 P3 richer, PROPOSAL-ONLY -- never mutates memory; uses deepseek-v4-pro, never messenger-chat/re)<br>`/usr/bin/python3 /home/lumen/minerva/lumen_reflector.py` | oneshot/no | OnCalendar=Sun *-*-* 18:30:00 UTC (lumen-reflector.timer) | — | — | — |
| `lumen-rerank.service` | active/running | Lumen local cross-encoder rerank service (wq#388, ab_rag consumer)<br>`/home/lumen/ab_rag/rerank/.venv/bin/python3 /home/lumen/ab_rag/rerank/rerank_server.py` | simple/always | daemon | — | — | — |
| `lumen-research-index.service` | inactive/dead | Lumen — embed research corpus into pgvector (idempotent, resumable)<br>`/usr/bin/python3 /home/lumen/minerva/retrieval/index_research.py` | oneshot/no | OnCalendar=*-*-* 10:00:00 UTC (lumen-research-index.timer) | After=lumen-pgvector.service | — | — |
| `lumen-telemetry.service` | inactive/dead | lumen-telemetry-v1 collector (wq#184/#185 persona proprioception)<br>`/bin/bash -c python3 /home/lumen/personas/telemetry/collect.py > /home/lumen/personas/telemetry/payload.json.tmp && mv /` | oneshot/no | OnUnitActiveSec=60; OnBootSec=30 (lumen-telemetry.timer) | — | — | — |
| `lumen-worklog.service` | inactive/dead | Regenerate Lumen messenger case-book (worklog.md) from episodes<br>`/usr/bin/python3 /home/lumen/personas/worklog_gen.py` | oneshot/no | OnUnitActiveSec=6h; OnBootSec=10min (lumen-worklog.timer) | — | — | — |
| `minerva-brief.service` | inactive/dead | Minerva — daily morning brief (auto-appends to claude_memory.md)<br>`/usr/bin/python3 /home/lumen/minerva/minerva_brief.py` | oneshot/no | OnCalendar=*-*-* 13:00:00 UTC (minerva-brief.timer) | After=minerva-eval.service | — | — |
| `minerva-classifier.service` | inactive/dead | Minerva — classify new intel items (two-axis relevance)<br>`/usr/bin/python3 /home/lumen/minerva/minerva_classifier.py` | oneshot/no | OnCalendar=*-*-* 02,06,10,14,18,22:30:00 UTC (minerva-classifier.timer) | — | minerva-proposal.service(After) | — |
| `minerva-defer-review.service` | inactive/dead | Minerva — reap finished defer research, then review the aged defer pile<br>`/usr/bin/python3 /home/lumen/minerva/minerva_defer_review.py review` | oneshot/no | OnCalendar=*-*-* 09:10:00 UTC (minerva-defer-review.timer) | — | — | — |
| `minerva-eval.service` | inactive/dead | Minerva — eval accepted proposals (safety checks before deploy)<br>`/usr/bin/python3 /home/lumen/minerva/minerva_eval.py` | oneshot/no | OnCalendar=*-*-* 03,07,11,17,19,23:00:00 UTC (minerva-eval.timer) | After=minerva-proposal.service | minerva-brief.service(After) | — |
| `minerva-factory.service` | inactive/dead | Minerva production factory - plan+apply GREEN, stage YELLOW (Frank-approved 2026-08-09)<br>`/usr/bin/python3 /home/lumen/minerva/minerva_factory.py run 15 --apply` | oneshot/no | OnCalendar=*-*-* 03,07,11,17,19,23:35:00 UTC (minerva-factory.timer) | After=docker.service | — | — |
| `minerva-proposal.service` | inactive/dead | Minerva — generate proposals from high-self-relevance intel<br>`/usr/bin/python3 /home/lumen/minerva/minerva_proposal.py` | oneshot/no | OnCalendar=*-*-* 02,06,10,14,18,22:45:00 UTC (minerva-proposal.timer) | After=minerva-classifier.service | minerva-eval.service(After) | — |
| `minerva-tag-sections.service` | inactive/dead | Minerva — re-tag claude_memory.md sections and rebuild embeddings<br>`/usr/bin/python3 /home/lumen/minerva/context/tag_sections.py` | oneshot/no | path (minerva-tag-sections.path) | — | — | — |
| `minerva-tag-sections.path` | active/waiting | Watch claude_memory.md for changes and re-tag sections | path | PathChanged: [Unit] | — | minerva-tag-sections.service(After) | — |

### 2.8 Messenger / persona / public surfaces (25 services, 14 timers, 1 paths)

| unit | state | what / ExecStart | type/restart | cadence (trigger) | deps | dependents | alert |
|---|---|---|---|---|---|---|---|
| `beszel-agent.service` | active/running | Beszel Agent — System metrics collector<br>`/home/lumen/beszel/agent/beszel-agent` | simple/always | daemon | After=beszel-hub.service | — | — |
| `beszel-hub.service` | active/running | Beszel Hub — System monitoring dashboard<br>`/home/lumen/beszel/beszel serve --http 0.0.0.0:8090` | simple/always | daemon | — | beszel-agent.service(After) | — |
| `fleet-inventory-render.service` | inactive/dead | Fleet inventory: render machines/*.yaml -> www/fleet.html (wq#1455)<br>`/usr/bin/python3 /home/lumen/fleet/inventory/render.py` | oneshot/no | path (fleet-inventory-render.path); OnUnitActiveSec=5min; OnBootSec=2min (fleet-inventory-render.timer) | — | — | — |
| `fleet-inventory-web.service` | active/running | Fleet inventory page, tailnet-only (http://100.117.66.102:8419/fleet.html, wq#1455)<br>`/usr/bin/python3 -m http.server 8419 --bind 100.117.66.102 --directory /home/lumen/fleet/inventory/www` | simple/on-failure | daemon | After=tailscaled.service | — | — |
| `fleet-status.service` | active/running | Lumen fleet status HTTP endpoint (port 7070)<br>`/usr/bin/python3 /home/lumen/fleet_status.py` | simple/always | daemon | — | — | — |
| `jay-watch.service` | **failed/failed** | jay-watch — GitHub activity watcher for SkinnnyJay collaboration<br>`/usr/bin/python3 /home/lumen/agents/jay-watch/jay_watch.py` | oneshot/no | OnUnitActiveSec=15min; OnBootSec=5min (jay-watch.timer) | — | — | — |
| `legal-resource-rebuild.service` | inactive/dead | Rebuild CA custody law reference (daily scrape)<br>`/usr/bin/python3 /home/lumen/legal-resource/build.py` | oneshot/no | OnCalendar=*-*-* 00/3:00:00; OnBootSec=3min (legal-resource-rebuild.timer) | — | — | — |
| `legal-resource.service` | active/running | CA custody law reference static site (public, no-Access)<br>`/usr/bin/python3 -m http.server 8130 --bind 127.0.0.1 --directory /home/lumen/legal-resource/site` | simple/always | daemon | — | — | — |
| `lumen-bench-checker.service` | inactive/dead | Bench eBay listing-status checker (oneshot)<br>`/usr/bin/python3 /home/lumen/projects/bench/checker.py` | oneshot/no | OnCalendar=*-*-* 01,07,13,19:20:00 UTC (lumen-bench-checker.timer) | — | — | — |
| `lumen-bench.service` | active/running | Bench salvage hardware tracker (bench.lumennode.uk)<br>`/usr/bin/python3 -m uvicorn app:app --host 127.0.0.1 --port 8135` | simple/always | daemon | — | — | — |
| `lumen-briefing.service` | inactive/dead | Lumen Daily Briefing for Frank<br>`/usr/bin/python3 /home/lumen/agent/daily_briefing.py` | oneshot/no | OnCalendar=*-*-* 08:00:00 (lumen-briefing.timer) | — | — | — |
| `lumen-community-watch.service` | inactive/dead | Lumen community watch (BC-250 community repos + Old Lamer YouTube -> ntfy)<br>`/usr/bin/python3 /home/lumen/services/lumen-community-watch/watch.py` | oneshot/no | OnUnitActiveSec=6h; OnBootSec=8min (lumen-community-watch.timer) | — | — | — |
| `lumen-dashboard.service` | active/running | Lumen Dashboard API — unified state aggregator<br>`/usr/bin/python3 /home/lumen/dashboard/dashboard_api.py` | simple/always | daemon | After=lumen-agent.service,lumen-monitor.service | — | — |
| `lumen-ebay-watch.service` | inactive/dead | Lumen eBay watch (tracked listings -> ntfy + memory ingest)<br>`/usr/bin/python3 /home/lumen/services/lumen-ebay-watch/watch.py` | oneshot/no | OnUnitActiveSec=30min; OnBootSec=5min (lumen-ebay-watch.timer) | — | — | — |
| `lumen-github-watch.service` | inactive/dead | Lumen GitHub watch (SkinnnyJay public activity -> ntfy)<br>`/usr/bin/python3 /home/lumen/services/lumen-github-watch/watch.py` | oneshot/no | OnUnitActiveSec=30min; OnBootSec=3min (lumen-github-watch.timer) | — | — | — |
| `lumen-lpp-frontier.service` | inactive/dead | LPP frontier — regenerate runtime/frontier.json cross-run progress state (wq#1066)<br>`/usr/bin/python3 /home/lumen/pokemon/tools/lpp_frontier.py` | oneshot/no | OnUnitActiveSec=15min; OnBootSec=4min (lumen-lpp-frontier.timer) | — | — | — |
| `lumen-mail-sync.service` | inactive/dead | Mirror lumen-mail D1 (public inbox threads) to /home/lumen/mailbot/mail.db<br>`/usr/bin/python3 /home/lumen/mailbot/mail_recall.py sync` | oneshot/no | OnUnitActiveSec=5min; OnBootSec=2min (lumen-mail-sync.timer) | — | — | — |
| `lumen-messenger.service` | active/running | Lumen Messenger Server<br>`/usr/bin/python3 -m uvicorn server_v2:app --host 0.0.0.0 --port 8765 --ws websockets --timeout-keep-alive 300` | notify/always | daemon | — | lumen-self-healer.service(After), lumen-self-healer.service(Wants) | ntfy-onfailure@lumen-messenger.service |
| `lumen-news-digest.service` | inactive/dead | Lumen daily intel digest (Phase 2) - tiered research digest to Messenger+ntfy<br>`/usr/bin/python3 /home/lumen/agent/lumen_news_digest.py` | oneshot/no | OnCalendar=*-*-* 16:00:00 (lumen-news-digest.timer) | — | — | — |
| `lumen-pokemon-live.service` | active/running | Lumen Plays Pokemon — live demo stream<br>`/usr/bin/npx tsx src/live_agent.ts` | simple/on-failure | daemon | — | — | lumen-kiosk-idle.service |
| `lumen-pr-watch.service` | inactive/dead | Lumen PR watch — poll Frank\x27s open upstream PRs, ntfy on change<br>`/usr/bin/python3 /home/lumen/services/lumen-pr-watch/watch.py` | oneshot/no | OnUnitActiveSec=30min; OnBootSec=5min (lumen-pr-watch.timer) | — | — | — |
| `lumen-weekly-report.service` | inactive/dead | Lumen Weekly Intelligence Report<br>`/usr/bin/python3 /home/lumen/agents/weekly-report/weekly_report.py` | oneshot/no | OnCalendar=Sun 09:00:00 UTC (lumen-weekly-report.timer) | — | — | — |
| `n8n.service` | active/running | n8n Workflow Automation<br>`/home/lumen/.npm-global/bin/n8n start` | simple/always | daemon | — | n8n-watcher.service(After), n8n-watcher.service(Wants) | — |
| `projects-app.service` | active/running | Lumen projects status page (port 8124)<br>`/usr/bin/python3 /home/lumen/projects_app.py` | simple/always | daemon | — | — | — |
| `system-map.service` | inactive/dead | Regenerate SYSTEM_MAP.md central system record (alerts Frank on change)<br>`/home/lumen/bin/system-map-update` | oneshot/no | OnCalendar=*-*-* 06:30:00 (system-map.timer) | — | — | — |
| `fleet-inventory-render.path` | **failed/failed** | Fleet inventory: re-render on inventory change (wq#1455) | path | PathChanged: /home/lumen/fleet/inventory/render.py | — | fleet-inventory-render.service(After) | — |

### 2.9 Power / plugs / telemetry (12 services, 10 timers, 0 paths)

| unit | state | what / ExecStart | type/restart | cadence (trigger) | deps | dependents | alert |
|---|---|---|---|---|---|---|---|
| `deepseek-cost-tracker.service` | inactive/dead | DeepSeek Daily Cost Tracker<br>`/usr/bin/python3 /home/lumen/agent/deepseek_cost_tracker.py` | oneshot/no | OnCalendar=*-*-* 23:59:00 (deepseek-cost-tracker.timer) | — | — | — |
| `lumen-budget-alert.service` | **failed/failed** | LiteLLM per-service budget alert checker (wq#719)<br>`/usr/bin/python3 /home/lumen/bin/litellm-budget-alert` | oneshot/no | OnCalendar=*-*-* 00/6:15:00 (lumen-budget-alert.timer) | — | — | — |
| `lumen-deepseek-spend.service` | inactive/dead | DeepSeek balance snapshot for spend history (CCC/LMCC bottom bars)<br>`/usr/bin/python3 /home/lumen/messenger/deepseek_spend_history.py record` | oneshot/no | OnUnitActiveSec=15min; OnBootSec=2min (lumen-deepseek-spend.timer) | — | — | — |
| `lumen-plug-baseline.service` | inactive/dead | Refresh per-node smart-plug wattage baselines (wq#591)<br>`/home/lumen/bin/plug-baseline --window 14d --write` | oneshot/no | OnCalendar=*-*-* 11:10:00 UTC (lumen-plug-baseline.timer) | Wants=lumen-powerdash-vmtunnel.service After=lumen-powerdash-vmtunnel.service | — | — |
| `lumen-plug-digest.service` | inactive/dead | Weekly smart-plug power digest to ntfy (wq#591)<br>`/home/lumen/bin/plug-findings --window 7d --digest` | oneshot/no | OnCalendar=Mon *-*-* 15:10:00 UTC (lumen-plug-digest.timer) | Wants=lumen-powerdash-vmtunnel.service After=lumen-powerdash-vmtunnel.service | — | — |
| `lumen-plug-findings.service` | inactive/dead | Push unprompted smart-plug power findings to ntfy (wq#591)<br>`/home/lumen/bin/plug-findings --window 24h` | oneshot/no | OnCalendar=*-*-* 15:05:00 UTC (lumen-plug-findings.timer) | Wants=lumen-powerdash-vmtunnel.service After=lumen-powerdash-vmtunnel.service | — | — |
| `lumen-plug-snapshot.service` | inactive/dead | Tapo plug power snapshot for Glance<br>`/home/lumen/bin/plug-snapshot` | oneshot/no | OnUnitActiveSec=1min; OnBootSec=2min (lumen-plug-snapshot.timer) | — | — | — |
| `lumen-powerdash-vmtunnel.service` | active/running | power-dash: SSH tunnel to VictoriaMetrics query API on Bes (127.0.0.1:8428 -> 18428)<br>`/usr/bin/ssh -N -T -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o StrictHostKeyChecki` | simple/always (NRestarts=6) | daemon | PartOf=lumen-powerdash.service | lumen-plug-baseline.service(After), lumen-plug-baseline.service(Wants), lumen-plug-digest.service(After), lumen-plug-digest.service(Wants), lumen-plug-findings.service(After), lumen-plug-findings.service(Wants) | — |
| `lumen-powerdash.service` | active/running | Lumen power-dash telemetry API + dashboard (lumennode.uk, internal)<br>`/usr/bin/node /home/lumen/power-dash/server/server.mjs` | simple/always | daemon | — | lumen-powerdash-vmtunnel.service(PartOf) | — |
| `vlogs-forwarder.service` | inactive/dead | wq#857 VictoriaLogs journal forwarder — ship journald to Bes<br>`/home/lumen/services/vlogs-forwarder/forward-journal.sh` | oneshot/no | OnUnitActiveSec=60; OnBootSec=30 (vlogs-forwarder.timer) | — | — | — |
| `wan-usage-poll.service` | inactive/dead | Poll GL-iNet BE9300 WAN byte counters into SQLite<br>`/home/lumen/bin/wan-usage-poll` | oneshot/no | OnUnitActiveSec=60s; OnBootSec=90s (wan-usage-poll.timer) | — | — | — |
| `wq394-netdata-sampler.service` | inactive/dead | wq#394 netdata canary — RAPL watts + netdata CPU% sample<br>`/usr/bin/python3 /usr/local/lib/wq394-netdata-canary/sampler.py` | oneshot/no | OnUnitActiveSec=5min; OnBootSec=1min (wq394-netdata-sampler.timer) | After=netdata.service | — | — |

### 2.10 Other (1 services, 1 timers, 0 paths)

| unit | state | what / ExecStart | type/restart | cadence (trigger) | deps | dependents | alert |
|---|---|---|---|---|---|---|---|
| `lumen-corrector.service` | inactive/dead | Lumen Corrector — auto-fix AUTO-SAFE findings, escalate the rest (Phase 3)<br>`/usr/bin/python3 /home/lumen/agents/corrector/lumen_corrector.py` | oneshot/no | OnUnitActiveSec=30min; OnBootSec=10min (lumen-corrector.timer) | — | — | — |

## 3. The guard / watchdog layer — what each one protects, how it signals, where it is blind

Conventions used below. **Signal**: how a finding leaves the process — `ntfy` (via `/home/lumen/bin/ntfy-send` unless "direct" = raw curl to :8080), `aq` (row in alerts.db), `Gatus` (external endpoint push), `wq` (ticket), `exit1` (non-zero exit → unit shows *failed*; only meaningful if an `OnFailure=` hook or the failed-units sweep sees it). **Edge** = edge-triggered (alerts once on transition). **Acts** = it mutates state (restart/kill/release) rather than only reporting.

### 3.1 Meta-watchdogs (watch the watchers / the alert path)

| guard | cadence | protects | signal | acts? | blind spot |
|---|---|---|---|---|---|
| `lumen-failed-units` | 5 min | *any* failed systemd unit, incl. stock distro oneshots (born from the 6-day logrotate miss) | ntfy + aq `failed-unit`, edge, fail-open exit 0 | no | **No notion of "failed by design".** Guards that exit 1 to signal (§0 #3) are reported as failures once and then sit in `--failed` forever; 37 open `failed-unit` aq rows today. Also cannot see a *timer* that stopped firing (inactive timer ≠ failed unit). |
| `lumen-cron-deadman` | 10 min | timer-driven units that opted in via `lumen-cron-stamp %n` — stamp older than 2× the timer's real interval → urgent | ntfy (urgent stale / high recovered) | no | **Coverage is 11 of 118 timers.** The 107 others (all backups, vault datasyncs, sentinel-probes, model-watchdog, every drift guard) have *no* dead-man: if their timer is stopped/disabled/never fires, nothing notices. This is the single biggest missing-watchdog class (§5 #3). |
| `alert-deadman-beat` + `deadman-killtest` | 5 min / quarterly | the whole on-fleet alert path — beats deadman.lumennode.uk **only** if self-hosted ntfy accepts a publish; silence → off-fleet email/ntfy alarm | off-fleet | no | Proves *publish* works, not that *subscribe/triage* works: ntfy up + `lumen-alert-queue` dead = beat continues, aq stops filling. Killtest is quarterly (last 08-28) — drill-marked, restores itself via trap. |
| `lumen-sentinel-watch` | 5 min | Gatus (:8418) itself — reciprocal watchdog | ntfy direct (urgent) on Gatus down | no | Level-triggered: re-pages every 5 min while Gatus is down (acceptable for a "fleet is unmonitored" condition). Hardcoded Anubis-migration text is stale (Gatus is on Lumen). |
| `lumen-alert-queue` (daemon, Type=notify) | — | persists ntfy `lumen-alerts,lumen-updates` into aq; progress-tied sd_notify watchdog (wq#1052) so a half-open SSE trips the systemd watchdog | systemd watchdog → Restart=always | no | Subscribes to two topics only. `lumen-fleet`, `lumen-heartbeat`, `lumen_alerts` (n8n typo) are not persisted → mcp-pressure-guard auto-restarts (→lumen-fleet) and dispatch-watchdog auto-recoveries never reach aq. |
| `lumen-aq-escalate` | 10 min | unacked CRITICAL *probe* rows (`source=probe, priority=high, count>=3`) → one P0 fleet-fires ticket + one page, at-most-once | wq + ntfy | files tickets | Scoped to `source=probe` only. failed-unit, jay-watch, drift, load rows never escalate to a ticket; they pile up (250 open). |
| `lumen-aq-autoack` / `lumen-alert-hygiene-audit` | daily / quarterly | ack stale info-class rows; 90-day rule audit | aq | acks | Autoack scoped to info-class; the 78 jay-watch rows are not info-class → untouched. |
| `lumen-gatus-ntfy-relay` (daemon :8784) | — | Gatus `custom` alert provider → ntfy-send (so Gatus alerts get OOB fan-out) | — | no | `Restart=on-failure`, no OnFailure hook, not in healer table, not port-checked by anything: if it dies Gatus alerts vanish silently until the next Gatus alert *and* someone notices it never arrived. **Missing watchdog** (§5 #5). |
| `lumen-command-receiver` (daemon, notify) | — | Frank's phone → ntfy `lumen-commands` → fleet actions | systemd watchdog | acts | Same subscribe-side blindness as alert-queue: nothing external proves it is receiving. |

### 3.2 Correctors / actuators (they restart or kill)

| guard | cadence | protects | signal | acts | hazard |
|---|---|---|---|---|---|
| `lumen-self-healer` (daemon, notify, progress-tied) | 60 s poll | declarative watch table (`healer_watch_table.yaml`): redis-server, messenger, cloudflared, memoryd, mcp-gateway, desktop-commander-mcp, pokemon-live, ntfy(docker), litellm(docker); ports memoryd/messenger/ntfy; RAM crit 90 % | ntfy after 3 failed restarts; holddown-aware (wq#967) | **restart** | Table `stall:` entry watches `lumen-agent` (masked since 2026-08-21) with `restart_cmd: None` → dead entry, harmless but stale. Baked FALLBACK table still names `lumen-prompt-engine`, `lumen-vip-api`, `lumen-vip-watcher` (not-found) — if the YAML ever fails to parse the healer falls back to restarting ghosts. It restarts `cloudflared` — the pipe Commander rides — on its own judgement; that is the documented outage vector if it fires mid-session. |
| `mcp-pressure-guard` (root) | 1 min | Commander gateway: PSI full avg60 ≥25 % or cgroup ≥11 GiB → restart (stateless, transparent) | ntfy **direct** to `lumen-fleet` (not persisted to aq) | **restart DC** | 10-min cooldown. Trip counts live only in journald (~11 h retention) → a nightly flap history is unrecoverable next morning. |
| `lumen-node-killer` (daemon) | continuous | RAM: reaps orphaned desktop-commander instances by cmdline identity (v2, never bare `node`) | ntfy edge on >20 kills/scan (aq#60 fix) | **kill** | Defense-in-depth behind `--sessionTimeout`; no restart-limit, no OnFailure. |
| `lumen-rosco` | 1 h | RAM: kills 7-day-old processes only when free <4 GB, protected-name allowlist | ntfy direct `lumen-updates` when it kills | **kill** | Name-allowlist (`node`, `python`… protected) — a stale `bash` fworker wrapper 7 days old is fair game. Overlaps node-killer and healer RAM-crit; three RAM actuators with no shared cooldown. |
| `lumen-guardian` (daemon, root) | continuous | hardware: temps/RAS from sysfs+rasdaemon → notify / throttle governor / **poweroff** | ntfy urgent/max | **shutdown** | Dry-run flag file gates real actions. Fails closed. Not in any healer table — if it dies, hardware protection is gone silently (Restart=always only). |
| `dispatch-watchdog` | 4 min | foreman dispatcher stall (runnable work, nothing spawns ≥2 passes) → recovery ladder (restart timer, start service) | ntfy `lumen-fleet` on recover / `lumen-alerts` urgent on failed recovery | **restart dispatcher** | 15-min cooldown. Recovery pages go to `lumen-fleet` → not in aq. |
| `lumen-foreman-selfheal` | 10 min | wq tickets blocked `INFRA_CRASH` → `wq release` under attempt/age caps | ntfy deduped | **releases tickets** | Intentionally leaves WORK_BLOCKED/LIMIT_HIT/HARNESS_DEFECT. |
| `lumen-liveness-clear` | 30 min | auto-closes Frank-gated tickets whose premise is already true on live state (LIVENESS_GATE §12.3) | wq | **closes tickets** | Acts on wq state from a heuristic — a false "premise met" silently closes work. Covered by cron-deadman stamp. |
| `lumen-tickle` | hourly | fires due deferred-work items into wq | wq | **creates tickets** | |
| `lumen-lpp-stuck-supervisor` | 30 min | LPP story_score/MODE stall → bounded intervene → auto-ticket → 3 h escalate | wq + ntfy | **intervenes in LPP** | |
| `lumen-rerank-thread-watch` | 5 min | 48 h auto-rollback of the 16-thread rerank arm on gateway restart or sustained PSI (wq#596) | ntfy + rollback script | **rollback** | Installed 2026-08-14 "48h auto-expiring" — still installed and firing every 5 min four weeks later; DONE-marker one-shot means it is now a no-op timer. Retire (§7 F-10). |
| `power-remediate` | 60 s | unreachable node → WoL → AC-cycle → human (policy-gated, `auto_boot_verified` required) | ntfy + aq | **ARMED=0 (staged)** | Dark. 3 open aq rows are `disarmed-would-act`. |
| `wadjet-ha-monitor` (root) | 30 s | vault primary probe → fence Ma'at, promote standby, flip CF origin, **restart cloudflared** | log + ntfy | **failover** | `AUTO_PROMOTE_ENABLED` flag = arm. The cloudflared restart is the Commander-outage vector; hence the detached-systemd-run rule. |
| `lumen-kiosk-idle` | OnFailure of pokemon-live | takes :8871 when the live stream crashes | — | serves idle page | ExecStartPre guard (`lpp-idle-guard`) refuses while live is `activating` → currently sits *failed* from a race at 06:33Z even though live is fine. Noise in `--failed`. |

### 3.3 Correctness probes (does it *work*, not is it *up*)

| guard | cadence | probes | signal | edge | gap |
|---|---|---|---|---|---|
| `sentinel-probes` (Tier-2) | 30 min | function-correctness: LLM synthetic call, wq health, invisible-work watch, pokemon liveness, R4 undetected-downtime sensors, … → Gatus external endpoints + aq granular rows | Gatus + aq `probe` + ntfy urgent on CRITICAL + digests | yes (edge-clear on PASS, wq#548) | Currently **FAILING**: `probe-synthetic-llm`, `probe-invisible-work-watch` (plus Gatus `mistral-small`, `khepri`, `3dprinter`). Only 2 open `probe` aq rows → count<3 so aq_escalate has not ticketed them yet, or they are flapping. Not stamp-covered by cron-deadman: if this timer stops, Gatus endpoints just go stale-green. |
| `lumen-model-watchdog` | 10 min | every LiteLLM rung incl. local `qwen36-local`; DOWN = 2 consecutive misses; never probes messenger-chat/re (bug #22249) | **Gatus only** (own ntfy voice removed) | — | Reports only via Gatus → depends on Gatus being read. gemini hourly cap. Not stamp-covered. |
| `lumen-public-guardian` | 2 min | the two public surfaces: email (D1 store freshness E1–E6) + LPP stream (S1–S4) — correctness with freshness assertions; heal ladder; fires tickets | ntfy RED/FIRE/RECOVERED + wq | yes | Stamp-covered. |
| `lumen-embed-guard` | hourly | `archival_notes.embedding` malformed rows + live search_v3 dense-path probe + v3-failure breadcrumb; **repairs** rows | ntfy + exit | — | After=ollama. Good design; note it *acts* (repair) — belongs in §3.2 too. |
| `litellm-auth-alert` ← `lumen-litellm-auth-monitor` | 2 min ← daemon | ≥5 auth-401 in 10 min → urgent + aq; single stragglers ignored | ntfy + aq, edge | yes | Monitor tails `docker logs litellm` → dies on every litellm recreate (NRestarts=8); alerter sees a gap for one window. Rotation mid-window can hide a burst (documented, accepted). |
| `langfuse-idle-alert` / `langfuse-evaluator(-smoke)` | 1 h / 15 min / weekly | zero traces in 6 h; LLM-as-judge 5 % sample; judge-path smoke | ntfy `lumen-updates` | — | wq#1648 **fixed** — waiting for 09-13 turned out to be unnecessary: the failure reproduces on demand via `python3 /home/lumen/services/langfuse-ops/evaluator.py --smoke --verbose`. It was *not* a pre-main condition failure; pid 3959735 really ran and exited 1 in the same second (`ExecMainCode=1`, i.e. CLD_EXITED). Cause was judge-alias rot: `mistral-small` carried 4 of 5 origins and is no longer a LiteLLM alias (400 "Invalid model name"), and `cerebras-llama` returned `content=None` at `max_tokens=60`. Judges are now `deepseek-chat`/`cerebras-llama`, budget 400, plus a `/v1/models` pre-flight that names alias rot instead of emitting a bare 400. Outcomes now append to `/home/lumen/logs/langfuse-evaluator.log`, so journald's ~11 h retention can no longer erase the postmortem. Unit stays `failed` until the 09-13 timer run clears it. |
| `lumen-load-watch` | 1 min | load5/nproc >1.5× for 10 min, hysteresis, re-page ≤ every REPAGE_S; aq row open/auto-resolve | ntfy + aq | yes | Good template — the reference design for the class. |
| `lumen-ecc-watch` / `lumen-ecc-rollup` | 5 min / daily | EDAC mc-level CE/UE deltas (UE = ESCALATE) / daily rollup | ntfy urgent / info | — | Host-gated (`hostname=lumen`). State in /var/lib. |
| `lumen-split-watch` | 10 min | agent5 split queues on Redis db5 + units `lumen-planner/generator/evaluator` | ntfy edge | yes | **Watches units that do not exist** and reports `ok depths=[206,0,0] dead=187`. The STALL rule needs "depth not decreasing for 30 min" — 206 has not decreased in weeks, so either the state file was seeded with 206 or the rule is broken; either way a dead pipeline reads as OK. Retire with agent5 or fix (§7 F-4). |
| `lumen-monitor` (daemon) | 60 s | writes `scratch/STATUS.json` (is-active of a named list + messenger endpoints) for Claude/dashboards | file | — | Allowlist-based (the pattern lumen-failed-units was built to escape); consumers of STATUS.json (healer stall entry, CCC bars) inherit the staleness. |
| `n8n-watcher` (daemon) | 60 s | n8n event log → real workflow failures | ntfy **direct to `lumen_alerts`** | — | **Wrong topic** (underscore). Zero of its alerts have ever reached aq or Frank's `lumen-alerts` subscription. (§7 F-6) |
| `fleet-auth-ssh-probe` / `gatus-sshauth-probe` | 5 min | synthetic auth-layer SSH to fleet nodes / push-model to Gatus | ntfy.sh fail-loud / Gatus | — | fleet-auth-ssh-probe unit description still says "fail-loud to ntfy.sh" (public ntfy.sh was retired per state store) — verify its target before trusting it. |
| `lumen-bc250-watch`, `lumen-community-watch`, `lumen-github-watch`, `lumen-pr-watch`, `jay-watch`, `repo-visibility-sentinel`, `git-guard-sentinel`, `lumen-ebay-watch` | 15 min–6 h | external-world tripwires (intel keywords, repos, PRs, public-repo allowlist, hook bypass, listings) | ntfy + aq | mostly edge | **jay-watch is failed** (dead token path, §7 F-8). `git-guard-sentinel.service` shows `UnitFileState=disabled` yet its timer is active and fires hourly — same label drift as §0 #5. repo-visibility needs no token (good). |

### 3.4 Drift / integrity guards (config, git, secrets, units)

| guard | cadence | asserts | signal + exit | acts? | gap |
|---|---|---|---|---|---|
| `unit-drift` (wq#1041) | 15 min | `/etc/systemd/system` + drop-ins + enable-state == git baseline in `/home/lumen/fleet-units` | ntfy info-class on drift (`--digest`), **exit 1** | no | Exit 1 with no OnFailure → the unit is *failed* whenever drift exists; today it is inactive/dead = baseline clean at 06:25Z. Every install this session (none) would flip it red. The digest is info-priority → not OOB, easy to miss. |
| `wq-drift-guard` (wq#886) | 5 min | `/home/lumen/bin/wq` byte-identical to git HEAD | ntfy urgent, **exit 1** | no | **Currently failed** because `/home/lumen/bin` is checked out on `feat/wq1374-orch-operability-session-budget-blocked-reap-evidence` (dirty). It cannot distinguish "wq clobbered" from "whole repo on a branch" — it fires the *clobber* alarm for a *branch* condition. |
| `primary-tree-guard` (wq#900) | 5 min + post-run in fworker | every PRIMARY tree on its default branch; auto-restore only when clean | ntfy on drift, **exit 1** while drifted, restores clean trees | restores | **Currently failed**: `/home/lumen/bin` drifted + dirty → "not restored", waiting for a human. Has been re-alerting every 5 min since ≥06:27Z (level-triggered by exit code; the ntfy side is deduped in-script). Root cause is one worker's leftover branch (wq#1374) — the *same* condition also trips wq-drift-guard: two guards, one incident, no ticket. **Filed as §7 F-1.** |
| `drift-assert` v2 (wq#393) | nightly 08:30Z | explicit GOLDEN fact set vs live fleet (sysctl, systemctl show, grep, ip over SSH) | ntfy edge on pass/fail-set change; report on disk | no | GOLDEN is hand-curated in the script — every state-store change needs a matching edit or it silently asserts the old world. |
| `dc-config-guard` (path + 5-min timer) | on change + 5 min | DC `config.json` tier-1 blockedCommands intact; refreshes last-known-good | re-runs enforcer | **rewrites config** | Path unit is loop-safe by design (only acts on drift). |
| `dc-patch-tripwire` (wq#416) | daily 09:00Z | DC dist patches (handle-store, wq#416 markers ≥6, sudo-strip, dc-config-atomic ≥3) present; version unchanged | ntfy urgent + aq `tripwire`, exit 1 | no | Daily only: an npm bump at 09:01Z is invisible for 24 h. `dc-config-guard` would catch the config half within 5 min but not the dist half. |
| `commit-hygiene-sweep` | daily 09:00Z | fleet git trees dirty/unpushed/behind | **aq only, no ntfy** | no | 1 open row. Excludes `/worktrees/`. |
| `lumen-repo-sweep` (wq#800) | 4 h | managed repos IN_SYNC via repo_reconcile | wq ticket per diverged repo, exit 1 | files tickets | |
| `lumen-pat-scan` (wq#523/565) | daily | no GitHub token literal outside the 4 canonical homes; world-readable check | ntfy via OnFailure, **exit 1** | no | **Currently failed** — every hit is `CRITICAL 644 …/worktrees/wq*/…/tests/test_*.py [WORLD-READABLE]`: test fixtures in *worktrees* containing token-shaped literals. 2 min 35 s CPU/run walking /home /etc /opt /srv. Needs a worktree/test-fixture exclusion or it will fail daily forever and desensitise the OnFailure page (§7 F-2). |
| `lumen-read-dir-sweep` (wq#1482) | daily 05:15Z | masked secret sweep of every `allow.read_dirs` root → runtime JSON; capability_lint fails closed on stale sweep | file | no | Stamp-covered. If it stops, capability grants fail closed (good). |
| `lumen-dispatch-lint` (wq#533) | daily | dispatch files not instructing retired rules | ntfy via OnFailure, exit 1 | no | |
| `lumen-ui-drift` (wq#770) | daily | cloudflared hostnames ↔ dashboard links ↔ origin identity (IDENTITY_CHANGED class) | ntfy + wq on 2nd cycle | files tickets | 1 open aq row. |
| `lumen-retirement-audit` (wq#723) | weekly Mon | retired_services.yaml entries not alive; orphan long-runners >7 d; units whose ExecStart is gone | aq + ntfy via OnFailure, exit 1 | no | Weekly: a retired service that comes back Tuesday runs 6 days. `retired_services.yaml` does not list `lumen-planner/generator/evaluator` (only sambanova) → retirement-audit cannot tell split-watch its targets are gone. |
| `lumen-trivy-weekly`, `gitea-version-check`, `lumen-sqlite-health`, `lumen-disk-hygiene`, `lumen-scratch-sweep`, `lumen-memory-audit`, `lumen-model-audit` | weekly/daily/12 h | CVEs, Gitea floor, SQLite integrity, dead-space, scratch retention (dry-run), pgvector drift, rung correctness | ntfy/aq | hygiene acts | `memory-audit`/`model-audit` timers carry "installed DISABLED" labels and are live (§7 F-3). |

### 3.5 Coverage matrix — who watches the watchers

| asset | liveness (is it up) | correctness (does it work) | dead-man (did it stop running) | acts on failure |
|---|---|---|---|---|
| ntfy container | healer (docker restart), deadman beat (off-fleet) | deadman beat (publish 200) | deadman (off-fleet) | healer |
| lumen-alert-queue | sd_notify watchdog (progress) | — | — (beat still succeeds) | Restart=always |
| lumen-gatus-ntfy-relay | — | — | — | Restart=on-failure only |
| Gatus | sentinel-watch (5 min) | — | — | — |
| LiteLLM | healer (docker), model-watchdog (rungs) | probe-synthetic-llm, model-audit, budget-alert | — | healer restart |
| foreman dispatcher | foreman-health (5 min), dispatch-watchdog (4 min), gates (nightly/Thu) | dispatch_health pass classification | cron-deadman stamp | dispatch-watchdog ladder |
| wq (queue.db + CLI) | wq-health stamp, sentinel wq probe | wq-drift-guard (file), self_heal | cron-deadman | self_heal releases |
| memoryd | healer (unit + :8139) | embed-guard (v3 path) | — | healer restart |
| messenger | healer (unit + :8765), OnFailure page | public-guardian (email/stream) | — | healer restart |
| Commander (DC :3100) | healer, mcp-pressure-guard | dc-config-guard, dc-patch-tripwire | — | pressure-guard restart, node-killer |
| cloudflared | healer, OnFailure? (none) | ui-drift (origins), vault 302 in handoff | — | healer restart, wadjet-ha restart |
| vault HA (Ma'at/standbys) | wadjet-ha-monitor 30 s | datasync timers, backup-critical | **none** — if wadjet-ha-monitor.timer stops, the vault is unwatched and nothing says so | auto-promote |
| backups (5 timers) | — | backup-verify monthly | **none** | — |
| the 107 non-stamped timers | lumen-failed-units (only if the *service* fails) | — | **none** | — |
| guardian (hardware) | — | — | — | Restart=always |
| self-healer itself | sd_notify progress watchdog (wq#1054) | — | — | Restart=always |

## 4. Failure-cascade analysis — "if X dies, what silently breaks"

Format: **X dies** → *immediate* (declared systemd effect) → *silent downstream* (port/file consumers with no declared edge) → *who notices, how fast* → *who does not*.

### 4.1 `docker.service`
- immediate: `Requires=` stops the 3 fleet researchers (they page via OnFailure). All 35 containers stop: litellm, ntfy, pgvector, gatus, langfuse, searxng/vane, dify, plausible, mongodb, minio, glance, actual.
- silent: **every alert path is gone at once** — ntfy-send fails → every guard's ntfy is lost; aq subscriber loses its upstream (its sd_notify watchdog keeps it alive but idle); OnFailure hooks post into nothing. Gatus gone → model-watchdog reports into nothing. LiteLLM gone → minerva chain, dgm, reflector, convo-learn, LPP escalate, sentinel synthetic probe all fail (mostly fail-loud to… ntfy).
- notices: **deadman beat stops** (ntfy publish ≠200 → no beat) → off-fleet alarm within the deadman grace window. That is the only survivor and it is the right one. Healer tries `docker restart ntfy` (kind=docker) — which cannot work with docker down; it then pages… via ntfy.
- does not: anything on-fleet. This is the designed "Lumen is dark" case and the deadman is the answer; keep the quarterly killtest honest.

### 4.2 `ntfy` container alone
- immediate: none declared.
- silent: identical to 4.1 for alerts, but LiteLLM/Gatus keep working so *work continues while every page is dropped*. mcp-pressure-guard, budget-alert, sentinel-watch, rosco, n8n-watcher post direct to :8080 and get connection refused; ntfy-send tries OOB for urgent/forced priorities only (`NTFY_OOB_PRIORITIES`) — info/default alerts are lost outright.
- notices: healer (docker restart, 60 s); deadman beat (off-fleet); ntfy-send prints `primary=<code>(FAIL)` to the caller's journal.
- does not: aq (rows simply stop arriving — indistinguishable from a quiet night). **Missing: an aq-side "no rows in N hours while ntfy is up" tripwire.**

### 4.3 `lumen-alert-queue` (subscriber) dies or wedges
- immediate: sd_notify watchdog restarts it (progress-tied; keepalive lines every 45 s).
- silent: while down, pages still reach Frank's phone (ntfy is up) but **nothing is persisted to aq** → aq_escalate never sees repeats → no fleet-fires tickets → `aq list open` looks healthy. deadman beat still succeeds (publish works).
- notices: Restart=always + watchdog. Nothing external.
- does not: anything that reads aq as the source of truth (session-start "aq triage", mission-control).

### 4.4 `litellm` container
- immediate: `lumen-litellm-auth-monitor` exits (tails its logs) → Restart=always loop until the container is back (NRestarts already 8 from routine recreates).
- silent: 3 fleet researchers stall on 4000 (they have notify watchdogs — a stalled loop trips them, OnFailure pages); minerva-classifier exits 1 (fail-loud), proposal/eval/factory skip; model-watchdog marks every rung DOWN → Gatus 6-models page; budget-alert cannot read keys → exit 1 → *another* failed unit; memoryd is unaffected (ollama embeds); messenger brain is unaffected (Claude CLI, not LiteLLM) — **the user-facing surface stays green while the entire autonomous lane is dead.**
- notices: healer (docker restart 60 s), model-watchdog (≤20 min, 2 misses), sentinel probe-synthetic-llm (30 min), researchers' OnFailure.
- does not: `lumen-foreman-dispatch` — fworkers run `claude` CLI, so dispatch keeps spawning workers whose LiteLLM-backed judges fail; self_heal classifies as WORK_BLOCKED, not INFRA_CRASH → tickets pile up blocked with no page (dispatch_health only alarms on *spawned=0*).

### 4.5 `cloudflared`
- immediate: none declared (healer restarts it within 60 s).
- silent: every public hostname 502s: vault.lumennode.uk (Frank's password manager!), claude/mctl/scribe/propose/bench/ask/law/pokemon stream, mcpw (Commander from claude.ai — **this audit's own transport**). LPP stream watchers see a dead stream; public-guardian S1–S4 go RED (2 min) and its heal ladder may *restart cloudflared again*. wadjet-ha-monitor only restarts it during failover.
- notices: healer (60 s), public-guardian (2 min, correctness), ui-drift (daily), `handoff verify` "vault.lumennode.uk: 302" line — but only when a session runs it.
- does not: **the 5 units with `OnFailure=` do not include cloudflared** → a crash-loop is quiet except through the healer's 3-strike page. **Two independent actuators (healer + public-guardian ladder + wadjet-ha) can restart the same tunnel with no shared cooldown** — the documented Commander-SIGKILL hazard is a restart storm waiting for a coincidence.

### 4.6 `desktop-commander-mcp` (:3100)
- immediate: `lumen-maint-resume` orders after it (boot only). node-killer keeps reaping its children.
- silent: claude.ai sessions lose the fleet (this one included); fworkers are unaffected (they run `claude` CLI locally); mcp-gateway unaffected.
- notices: healer (60 s restart), mcp-pressure-guard (only on PSI/mem, not on death), `/health` endpoint via Gatus if configured.
- does not: no OnFailure, no port check in healer table (only messenger/memoryd/ntfy ports) → a hung-but-active gateway (the wq#380 class) is invisible to the healer; only pressure-guard's cgroup-size trip catches the bloat variant.

### 4.7 `redis-server`
- immediate: none declared (healer restarts; researchers/skillhex/intel `After=` only).
- silent: fleet researchers' claim/heartbeat (Redis-backed queue), agent5 split queues (db5 — already dead), lumen-intel, skillhex. Everything else is SQLite.
- notices: healer 60 s.

### 4.8 `ollama`
- immediate: memoryd `Wants=` (start-only), embed-guard `After=`.
- silent: memoryd embeds fail → search_v3 dense path degrades → **the exact wq#465 incident** (fleet silently drops to v2 recall). tag-sections path unit, research-index, `qwen36-local` offline rung (via LiteLLM → model-watchdog DOWN), pelican-bench.
- notices: embed-guard (hourly, live v3 probe → ntfy), model-watchdog (qwen36-local rung, ≤20 min), healer? — **ollama is NOT in the healer watch table** → no auto-restart beyond Restart=always. If it hangs (active but not answering — the 2026-08-10 timeout shape) nothing restarts it for up to an hour, and then embed-guard only alarms.

### 4.9 `lumen-memoryd` (:8139)
- immediate: none.
- silent: lane recall fails open to "" then to the legacy router (`MEMORY_V4_MEMORYD=1` client) → **workers keep running with worse memory and no error** — the "fail-open client makes a broken integration look like a working one" lesson in the handoff.
- notices: healer (unit + port 8139, 60 s). Good.
- does not: any *quality* signal — recall-bench is nightly and measures search_v3 directly, not through memoryd, so a memoryd auth/contract failure that leaves search_v3 fine is invisible to the bench.

### 4.10 `sentinel-gatus` container
- silent: model-watchdog and sentinel-probes push into a void (they report via Gatus only / Gatus + aq); gatus-ntfy-relay idles; Gatus's own alert thresholds never fire → **model rung outages become invisible** (model-watchdog has no own ntfy voice since wq#… "6-models alerts page").
- notices: lumen-sentinel-watch (5 min, urgent, level-triggered). Healer: no (not in table).

### 4.11 `lumen-foreman-dispatch.timer` stops (disabled/masked/lost in a unit-drift apply)
- silent: no fworkers spawn; the queue grows; nothing "fails".
- notices: cron-deadman (stamp `lumen-foreman-dispatch` older than 40 min → urgent), dispatch-watchdog (needs pass logs — a *never-running* dispatcher writes no passes; it checks `systemctl is-active` of the timer as part of its ladder), foreman-gate@nightly (22:00 PT proves next pass can dispatch, OnFailure page).
- This is the best-covered timer on the box — the template for §5 #3.

### 4.12 `wadjet-ha-monitor.timer` stops
- silent: vault primary unwatched; a Ma'at outage now needs a human to notice vault.lumennode.uk is down (public-guardian does not check vault; handoff verify does, only at session start).
- notices: **nobody**. Not stamp-covered, not in healer, no OnFailure. Frank's password manager availability rests on a 30-s timer with no dead-man. **§5 #4.**

### 4.13 backup timers stop (`lumen-backup-critical/important/offsite/prune/verify`)
- silent: restic repos age; offsite copy to Thoth ages; verify is monthly.
- notices: **nobody** until backup-verify (1st of month) — and that only checks repo integrity, not snapshot recency. The 2026-06 "stale restic lock from a boot-interrupted run" was found by a human. **§5 #4.**

### 4.14 `/home/lumen/bin` on a feature branch (today's live case)
- silent: **every** guard/tool that is a script in bin/ (ntfy-send, wq, aq, lumen-run, unit-drift, drift-assert, dc-patch-tripwire, litellm-auth-*, budget-alert, plug-*, lumen-failed-units, retirement-audit, ecc-watch, deadman beat…) executes *whatever that branch has* — production running unreviewed code, the wq#900 premise. Today the branch is `feat/wq1374-orch-operability-session-budget-blocked-reap-evidence` (dirty).
- notices: primary-tree-guard (5 min, exit 1, refuses to restore a dirty tree), wq-drift-guard (5 min, urgent "wq DRIFT" — a mislabel), both since ≥06:25Z. Neither files a ticket; both re-fail every 5 min; aq gets one edge row each from failed-units. **No ticket exists → filed now, §7 F-1.**

### 4.15 The alert-fatigue cascade (the ticket's own example, generalised)
Dead guard → its alert keeps re-firing or its target keeps re-failing → aq fills (250 open) → the reader stops reading → a *new* real alert lands in the same pile → undetected outage. Today's contributors: jay-watch (78 rows, failing every 15 min since token move), failed-unit (37 rows, 4 of them design-exits), ntfy:lumen-alerts (84 rows, un-escalated because `source≠probe`). The mechanism that turns a persistent condition into *one* ticket (aq_escalate) is scoped to probes only, so the other 240 rows have no exit path except a human.

## 5. Ranked single points of failure and missing watchdogs

Ranking = blast radius × how quietly it fails × how long until a human would notice without the fleet telling them. "Fix" is the class-level fix, not the instance.

| # | SPOF / gap | blast radius | how it fails | who notices today | class fix |
|---|---|---|---|---|---|
| **1** | **No convention for guard exit codes.** Guards signal findings with exit 1 (pat-scan, wq-drift-guard, primary-tree-guard, unit-drift, budget-alert, retirement-audit, repo-sweep, dispatch-lint, git-guard-sentinel, repo-visibility-sentinel) while others deliberately exit 0 + ntfy (dispatch_health, failed-units, load-watch, litellm-auth-alert). Result: `systemctl --failed` / `handoff verify` / failed-units sweep all conflate "guard fired" with "guard crashed". | every operator surface that shows red | permanently, quietly — red becomes wallpaper | nobody; 4/8 failed units today are this | Pick one contract and enforce it in `unit-drift`'s baseline lint: a guard either (a) exits 0 always and reports via ntfy+aq (**preferred** — the load-watch template), or (b) exits 1 *and* declares `OnFailure=ntfy-onfailure@%n` *and* sets `SuccessExitStatus=` so a *finding* is not a unit failure. Add a `lumen-failed-units` allowlist keyed off a unit-file tag (`X-Lumen-Guard=finding-exit`) so it reports "guard fired" (info, aq) vs "guard crashed" (urgent). |
| **2** | **aq triage saturation** (250 open; escalation scoped to `source=probe`) | the human layer — Frank reads a pile, not a queue | monotonic growth | nobody (alert-hygiene-audit is quarterly) | Extend `aq_escalate` with per-source rules: `failed-unit` count≥N → one ticket per unit; `jay-watch`/any source with ≥50 open rows → one "source is spamming" ticket + auto-suppress. Autoack rule should cover rows whose *source unit* is currently failed-by-design. |
| **3** | **107 of 118 timers have no dead-man.** cron-deadman covers 11 stamps. A timer that is stopped, masked, lost to a `unit-drift apply`, or whose `Persistent=false` run is skipped never produces a *failure* — it produces silence. | backups, vault HA, sentinel-probes, model-watchdog, every drift guard, datasyncs | silently, indefinitely | nobody (unit-drift would flag enable-state drift, but only if the *baseline* had the timer enabled and someone reads an info digest) | Make the stamp automatic: a systemd drop-in generator (`/etc/systemd/system/<name>.service.d/99-cron-stamp.conf` with `ExecStartPost=-/home/lumen/bin/lumen-cron-stamp %n`) applied to every timer-triggered fleet unit by `unit-drift snapshot`, so opting *out* is the exception. cron_deadman already derives cadence from `list-timers`, so coverage becomes total with no per-unit work. |
| **4** | **Vault HA monitor + backup chain have no watchdog of their own** (4.12, 4.13) | Frank's password manager; every restore path | timer stops → nothing | nobody | Covered by #3 automatically; additionally `handoff verify` should assert `wadjet-ha-monitor.timer` *and* `lumen-backup-critical.timer` `LastTriggerUSec` < 2× interval, and `backup-critical` should stamp snapshot age into Gatus (push endpoint) so *recency* — not just repo integrity — is monitored. |
| **5** | **Alert-path daemons with no external check**: `lumen-gatus-ntfy-relay` (:8784), `lumen-alert-queue` subscribe side, `lumen-command-receiver` subscribe side, `lumen-guardian` (hardware shutdown!), `ollama` (not in healer table) | Gatus alerts vanish; aq stops filling; phone commands ignored; thermal protection gone; recall silently degrades | daemon dead or wedged but `active` | Restart= only; alert-queue has sd_notify | Add all five to `healer_watch_table.yaml` with port checks (8784, 11434) and, for the two subscribers, a synthetic round-trip: publish a `lumen-heartbeat` row every 5 min (the deadman beat already does) and assert it appears in `alerts.db` within 2 min — proves subscribe→persist, not just publish. |
| **6** | **Dead guards over dead systems** (split-watch → agent5 units gone; healer `stall:` → lumen-agent masked; healer FALLBACK → 3 ghosts; `retired_services.yaml` missing agent5) | false "ok" from split-watch; a YAML parse error would make the healer restart ghosts | silently | nobody — retirement-audit checks *retired things alive*, not *guards watching retired things* | Add a retirement-audit check D: every unit/container/port named in `healer_watch_table.yaml`, split-watch UNITS, `lumen_status_monitor.py` svc list, `aq_escalate` RUNBOOKS and `drift-assert` GOLDEN must resolve to a loaded unit / running container — a guard that names a ghost is itself an alert. Retire `lumen-split-watch` with agent5 or fix its STALL rule (206 in plan_in is a stall). |
| **7** | **`Restart=always` with no `StartLimitBurst` ceiling and no restart-count alarm** on 41 daemons | a permanently broken dependency (e.g. a tunnel to a decommissioned host) becomes an infinite quiet loop, NRestarts climbing forever | quietly | nobody (litellm-auth-monitor=8, vmtunnel=6 today) | Sentinel probe: any fleet daemon with `NRestarts` delta > K in 24 h → aq row (edge). One `systemctl show -p NRestarts` sweep, 20 lines, in `sentinel_probes.py`. |
| **8** | **Three uncoordinated cloudflared restarters** (healer, public-guardian heal ladder, wadjet-ha-monitor) + Commander rides that tunnel | mid-session SIGKILL of whatever Claude is doing; a restart storm if two fire in the same minute | coincidence | nobody | Single actuator: route every cloudflared restart through `holddown`/`slock cloudflared` with a 5-min cooldown; the other two *request* a restart (aq row) instead of doing it. |
| **9** | **Three uncoordinated RAM actuators** (node-killer continuous, rosco hourly, healer ram crit 90 %) | a healthy 7-day-old fworker wrapper reaped by rosco; simultaneous kills | rare | logs only | Shared cooldown file + one owner; rosco's name-allowlist should include `fworker`/`lumen-run` wrappers. |
| **10** | **journald retention ~11 h** (supergateway ~774k lines/day vs SystemMaxUse=1G) — the langfuse-smoke failure reason is already gone; mcp-pressure-guard trip history is unrecoverable | every post-mortem older than half a day | already happened (§7 F-11) | rerank-thread-watch worked around it privately; wq#1648 did the same for langfuse-evaluator (appends outcomes to `/home/lumen/logs/langfuse-evaluator.log`), which fixes that one service but leaves the class open — raising `SystemMaxUse` needs root, which fleet workers are denied | `LogNamespace=` or `StandardOutput=append:` the gateway to its own file with logrotate; or raise journald cap and set `RateLimitBurst` on the gateway. vlogs-forwarder ships to Bes every 60 s — confirm Bes retention (90 d) actually *has* the gateway lines, then §7 F-11 can be answered from Bes. |
| **11** | **`fleet-inventory-render.path` self-DoS** (no `StartLimitIntervalSec=0`; each YAML write retriggers) | inventory page stale until the 5-min timer; path unit dead until daemon-reload/reset-failed | already happened 05:39Z | nobody (timer masks it) | `StartLimitIntervalSec=0` on the service + `TriggerLimitIntervalSec=`/`TriggerLimitBurst=` on the path (systemd ≥250) or debounce in render.py; add `.path` units to the failed-units sweep's *urgent* class since they never self-recover. |
| **12** | **Label drift on "STAGED / DISABLED / Frank-gated" timers** (familiarity, model-audit, skillhex, memory-audit, judgearena, pelican, foreman-dispatch/health/selfheal "STAGED — Frank enables") | Frank believes X is off; X is on (or vice-versa) | permanent | nobody | Descriptions must not encode enable-state. `unit-drift check` should fail if a `Description=` contains `DISABLED|STAGED|Frank-gated` while the timer is `enabled+active`. |
| **13** | **Dangling `After=litellm.service` / `lumen-pgvector.service`** | boot ordering not as documented | benign | nobody | Replace with `After=docker.service` + an `ExecStartPre` that waits for the port; `unit-drift` lint: every `After/Wants/Requires` target must be a loadable unit. |

### 5.1 Missing watchdogs, shortest list
1. Universal timer dead-man (auto-stamp drop-in) — closes #3, #4.
2. Subscribe-side round-trip for `lumen-alert-queue` / `lumen-command-receiver` — closes #5 (half).
3. NRestarts-delta probe — closes #7.
4. Guard-naming-a-ghost check in retirement-audit — closes #6.
5. Failed-units: "finding-exit" allowlist + `.path` urgency — closes #1 (visibility half), #11.
6. Backup snapshot-age push to Gatus — closes #4 (recency half).
7. `lumen-gatus-ntfy-relay`, `ollama`, `lumen-guardian` into the healer table — closes #5 (rest).

## 6. Current failed units (8 at capture) — verdict per unit

| unit | since | why (journal) | verdict | action |
|---|---|---|---|---|
| `primary-tree-guard.service` | ≥06:27Z, re-fails every 5 min | `DRIFTED /home/lumen/bin: on 'feat/wq1374-orch-operability-session-budget-blocked-reap-evidence' (dirty) — not restored` | **guard working; real incident** — production `bin/` is on a dirty feature branch | **F-1** ticket: reconcile the branch (commit/stash via lumen-pr worktree, checkout main); guard clears itself. |
| `wq-drift-guard.service` | ≥06:25Z, every 5 min | wq differs from git HEAD (because the whole repo is on that branch) | guard working; **same incident as above, mislabelled as "clobber"** | F-1; plus make the guard say "repo on branch X" when `HEAD != default` instead of "wq DRIFT". |
| `lumen-pat-scan.service` | daily since ≥09-09 | `CRITICAL 644 …/worktrees/wq{869,879,913}-a*/…/tests/test_*.py [WORLD-READABLE]` | guard working on a **false-positive class** (token-shaped literals in test fixtures inside worktrees) | **F-2**: exclude `worktrees/*/…/tests/` fixtures by fingerprint allowlist, or make fixtures obviously fake (`ghp_` + `FAKE`); 2.5 min CPU/run also argues for scoping. |
| `lumen-budget-alert.service` | 06:15Z, every 6 h | `ALERTED: 1 key(s) over 80%` → exit 1 | guard working — a key is at ≥80 % of its 30-day budget; **exit 1 is the design** | which key is above 80 % is in the journal before the tail I captured; note in F-9 ticket to raise/rotate; and this is the poster child for §5 #1. |
| `jay-watch.service` | every 15 min since token move (wq#1581, 09-09) | `RuntimeError: no GitHub PAT in agent .env` | **broken** — reads the dead `agent/.env` GITHUB_TOKEN path; 78 aq rows | **F-8**: point `token()` at `/home/lumen/.secrets/github-frank.token` (per HANDOFF) and resolve the 78 rows. |
| `fleet-inventory-render.path` | 05:39:45Z | `unit-start-limit-hit` after 13 renders in ~15 s (wq#1580 YAML writes) | **broken permanently** (path units do not recover) | **F-7**: `StartLimitIntervalSec=0` + trigger limit + `systemctl reset-failed`; timer masks it meanwhile. |
| `lumen-kiosk-idle.service` | 06:33:59Z | `lpp-idle-guard: lumen-pokemon-live is 'activating' — it owns :8871, refusing idle start` (×2, `Restart=always` gave up) | **race noise** — OnFailure of pokemon-live fired while live was restarting; the guard correctly refused; live is now running | Low: make the idle guard exit 0 + `RemainAfterExit` no-op when live is *activating* (SuccessExitStatus=3), so a refused start is not a failure. Folded into F-9. |
| `langfuse-evaluator-smoke.service` | Sun 09-06 10:50Z | **no journal entries** (retention); ExecMainStartTimestamp set, pid 0, status 1 | **judge-alias rot** — `mistral-small` retired from LiteLLM (400) on 4 of 5 origins; `cerebras-llama` returned `content=None` at `max_tokens=60`, crashing `.strip()` | **F-11 closed (wq#1648)**: no capture needed — the smoke path is deterministic, so running it by hand reproduced the exit-1 exactly. Fixed, not retired: judges re-pointed, token budget raised, alias pre-flight added, outcomes written to `/home/lumen/logs/langfuse-evaluator.log`. |

Also *active but wrong*: `lumen-split-watch` (ok over a dead pipeline), `n8n-watcher` (wrong topic), 6 "DISABLED"-labelled timers live, `lumen-rerank-thread-watch` (48 h watch now 27 days old, no-op).

## 7. Findings → tickets (filed 2026-09-10 during this audit; every defect named above has a ticket)

| id | pri | finding | ticket |
|---|---|---|---|
| F-1 | P1 | `/home/lumen/bin` primary tree on dirty feature branch (wq1374) — two guards failing every 5 min, production tools running branch code | **wq#1653** (fleet) |
| F-2 | P2 | pat-scan fails daily on worktree test fixtures (false-positive class, 2.5 min CPU) | wq#1642 |
| F-3 | P1 | six timers labelled "installed DISABLED / Frank-gated" are live and running; broker timers say STAGED | **wq#1654** |
| F-4 | P2 | split-watch guards the retired agent5 split, reports `ok` over 206 stuck + 187 dead; healer stale/ghost entries | wq#1643 |
| F-5 | P2 | retirement-audit should flag guards that name ghosts (check D) | wq#1644 |
| F-6 | P1 | n8n-watcher posts to `lumen_alerts` (unread topic) — silent black hole | **wq#1645** |
| F-7 | P2 | fleet-inventory-render.path dead from start-limit; path units never recover; timer masks | wq#1655 |
| F-8 | P1 | jay-watch reads dead `agent/.env` token path since wq#1581; 78 aq rows | **wq#1646** |
| F-9 | P1 | `lpp-cerebras` LiteLLM key at 100 % of $10 budget (budget-alert exit 1 = failed unit) | **wq#1647** (fleet) |
| F-10 | P3 | retire lumen-rerank-thread-watch (48 h watch, 27 days old, no-op) | wq#1656 |
| F-11 | P3 | ~~langfuse-evaluator-smoke fails weekly, evidence expired with journald; capture 09-13~~ **CLOSED 09-11**: reproduced on demand instead of waiting; judge-alias rot + `content=None` crash, both fixed; service now logs outside journald | wq#1648 |
| F-12 | P1 | **class:** guard exit-code convention (finding vs crash) + failed-units finding allowlist | **wq#1657** |
| F-13 | P1 | **class:** universal timer dead-man (auto cron-stamp drop-in) — 107/118 timers uncovered incl. vault HA + backups | **wq#1658** |
| F-14 | P2 | aq escalation beyond `source=probe`; spam-source suppression (250 open rows) | wq#1649 |
| F-15 | P2 | healer table gaps (gatus-ntfy-relay, ollama, guardian) + subscribe-side round-trip proof | wq#1650 |
| F-16 | P3 | sentinel probe on NRestarts delta (41 `Restart=always` daemons, no ceiling) | wq#1651 |
| F-17 | P2 | single cloudflared actuator with cooldown (3 restarters); same for RAM actuators | wq#1652 |
| F-18 | P3 | dangling `After=litellm.service` / `lumen-pgvector.service`; unit-drift target lint | wq#1659 |

Prior art the fixes should read first: wq#1027 (wq-drift-guard exits 1 every 5 min — the *same* class as F-12, fixed as an instance), wq#900/#929/#1108/#1313 (primary-tree drift incidents — F-1 is the fourth), wq#968 (failed-unit echo fix), wq#1184 (cron-deadman, the F-13 base), wq#965 (aq_escalate, the F-14 base), wq#971 (healer table, the F-15 base).

## 8. Appendix — reproduce

```
# 1. dump (read-only; ~20 s)
S=/tmp/<scratch>; mkdir -p $S; python3 foreman/research/tools/service_atlas_dump.py $S
#   -> $S/units.json (627 units × 50 props), $S/unitfiles/<unit> (300 fleet unit files + drop-ins)
# 2. catalogue (§2)
python3 foreman/research/tools/service_atlas_catalogue.py $S > /tmp/catalogue.md
# 3. failed units + reasons
systemctl list-units --failed --plain --no-legend; journalctl -u <unit> -n 12 -o short-iso
# 4. what nobody stamps
comm -23 <(systemctl list-timers --plain --no-legend | awk '{print $NF}' | sed 's/.timer$//' | sort) \
         <(ls /home/lumen/logs/.stamp.* | sed 's#.*/.stamp.##' | sort)
```

Hand-written sections were derived from: the 49 guard-script headers + alert/exit call sites (`grep -nE 'sys.exit|exit [1-9]|ntfy|aq add|wq add|systemctl (restart|stop)'`), `docker ps -a`, `sqlite3 alerts.db "select source,count(*) … status='open'"`, `curl :8418/api/v1/endpoints/statuses`, `healer_watch_table.yaml`, and the journals of the 8 failed units. All commands are read-only; no `systemctl reset-failed`, no restarts, no edits were made during capture.
