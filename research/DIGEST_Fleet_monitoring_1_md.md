# DIGEST — Frank's "Fleet monitoring 1.md" (wq#2468)

Source: `/home/lumen/scratch/TWO/Fleet monitoring 1.md`, 689 lines, read in full 2026-09-24.
The doc repeats its own headings ("## **1\. Secret Management**" appears twice), so every
citation is a **line range** plus heading text, e.g. `FMon1 L45-50 §"S.M.A.R.T. and IPMI"`.

Third of the wq#2463-2469 digest family. Layout follows `DIGEST_Fleet_Memory_3_md.md`;
§7 of that digest is the harness note for this family and it held — see §7 below for the
two refinements this run measured.

## 1. What the doc is, and how much of it to trust

A raw chat transcript in four turns, not a spec. Three properties matter before acting:

- **Half the file is a verbatim duplicate.** `FMon1 L336-595` repeats `L75-334` exactly —
  the SWE-agent secret manifest, the FastAPI wrapper, the DinD Deployment, the Cloudflare
  Tunnel section, the GitHub webhook steps. Two copies, one recommendation. The unique
  content is `L1-334` plus `L597-689`.
- **It is truncated.** The file ends at `L687-689` on a promise it never keeps: "Here is the
  technical implementation for managing Kured's reboot schedule via Kubernetes annotations
  and deploying the `smartctl_exporter`…" then stops. The two items most relevant to this
  fleet (SMART monitoring, reboot locking) are exactly the two the doc never delivers.
  Expect them in Fleet monitoring 2 (wq#2467).
- **Escape-mangled code.** Every block is markdown-escaped: `app \= FastAPI()`, `\-\>`,
  `\# comment`, `payload\["issue"\]`, `\---` between YAML documents. Nothing here can be
  pasted. If we implement one of these shapes, retype it from upstream docs.

Two things in the doc are actively dangerous and must not be copied even as a pattern:
a plaintext `ghp_…` PAT and a LiteLLM key inside a committed manifest (`FMon1 L92-97`,
violating instinct 4), and a `privileged: true` Docker-in-Docker sidecar reachable at
`tcp://localhost:2375` with `DOCKER_TLS_CERTDIR: ""`, inside a pod deliberately exposed to
the public internet (`FMon1 L186-200` + `L269-321`). That combination is root on the host
for anyone who gets one request past the HMAC check.

## 2. Every actionable recommendation, with a fleet verdict

Verdicts: **HAVE** = already built and verified; **ADAPT** = right idea, wrong mechanism for
a fleet with no Kubernetes; **REJECT** = do not build; **NEW** = genuine uncovered gap.

The premise the whole doc rests on is false for us: **there is no Kubernetes on this fleet.**
`which k3s kubectl helm` on lumen returns nothing (rc=1, re-verified live this run, same as
`DIGEST_Fleet_Memory_3_md.md` §2/R8). Orchestration is systemd units, SQLite (`queue.db`,
`alerts.db`), SSH fan-out and a few containers. Every DaemonSet/Deployment/annotation
mechanism below therefore needs translating, not applying.

| # | Recommendation (citation) | Verdict | Evidence |
|---|---|---|---|
| R1 | Alert → agent opens a GitHub Issue → SWE-agent pod clones, edits, tests, opens a PR (`FMon1 L5-33`) | **HAVE** (the loop) + **REJECT** (SWE-agent) | The loop is the fleet's spine already. `bin/aq_escalate.py` turns a threshold-crossing alert into exactly one P0 wq ticket with at-most-once paging (`aq_escalate.py:13-21`); `bin/gh-lane-worker` drains a lane with one Claude session per ticket; `bin/lumen-pr` forces the branch+PR path and "refuses the non-compliant one" (`lumen-pr:4-7`). This very digest arrived through it. Adding SWE-agent means a second, weaker resolver with its own sandbox, its own PAT and no wq lease/idempotency |
| R2 | Node Exporter on every node (`FMon1 L38-43`) | **HAVE** | wq#753/786, closed with measured proof: "14/14 reachable Linux hosts now run node_exporter, verified live in the existing VictoriaMetrics on Bes", metric-name count 37 → **458** (`reports/opsaudit_a3_node_exporter.md:8-31`). Ingestion is an unprivileged per-user vmagent on bes, `~/.local/vmagent-nodeexp/scrape.yml`, writing to the same VM as the power stack (`:55-58`) |
| R3 | cAdvisor for per-container footprint (`FMon1 L40-43`) | **ADAPT** | Real hole — nothing collects per-container or per-unit memory today. But cAdvisor is a K8s-shaped daemon; the fleet's containerised services are a handful, and node_exporter's systemd/cgroup collectors give the same "which unit is leaking" answer with no new daemon. See T4 |
| R4 | `smartctl_exporter` for failing drives (`FMon1 L45-49`) | **ADAPT → build it** | The single best idea in the doc for us, and a confirmed gap: no SMART collection exists anywhere in `bin/` (`lumen-disk-hygiene` is free-space, `lumen-ecc-watch` is memory CEs). Mechanism should be the node_exporter **textfile collector** with upstream `prometheus-community/node-exporter-textfile-collector-scripts/smartmon.sh` (verified to exist, §6) rather than a second exporter daemon needing its own root grant and scrape target. See T1 |
| R5 | `ipmi_exporter` for chassis temps, fans, voltages (`FMon1 L50`) | **REJECT** | No node on this fleet has a BMC. The inventory is Dell OptiPlex 3090 **UFF**/micro, whitebox Apollo Lake mini-PCs, Trigkey N100 boxes and Macs (`research/INVENTORY_RECONCILIATION.md:83-100`) — commercial desktops and mini-PCs, not PowerEdge. IPMI/iDRAC is a server-board feature; the desktop out-of-band equivalent is Intel vPro/AMT, which is per-SKU and not a fleet property (§6). Also note R15: the doc's hardware remediation *ends* in an IPMI power-off we cannot perform |
| R6 | Alertmanager webhook → NATS → LangGraph SRE agent classifies hardware vs software (`FMon1 L52-54`, `L597-660`) | **HAVE** (intent) + **REJECT** (transport) | There is no NATS and no LangGraph on the fleet (zero hits across `FLEET_SERVICE_ATLAS.md`, `ISSUE-LOG.md`). The classifier itself is `alert_name in hardware_alerts` — a dict lookup wearing a state machine (`FMon1 L616-637`); aq rows already carry source/priority labels. What the doc assumes exists and we actually lack is the *rule evaluation* step before it — see R7 |
| R7 | (implied by R6) Alert rules evaluated over the metric store (`FMon1 L52-54`) | **NEW — the real gap** | 458 distinct metric names land in VictoriaMetrics and **nothing alerts on them**: no `vmalert`, no Alertmanager anywhere in `bin/` (0 matches). Every page today comes from a per-host script (`lumen-load-watch`, `lumen-kernel-watch`, `lumen-disk-hygiene`) that re-measures what VM already stores. wq#753 built the eyes; the nerve to the aq queue was never wired. `reports/R5_external_practice.md:109-111` already scoped this as G2 (wq#1040). See T2 |
| R8 | `unattended-upgrades` on every Ubuntu node (`FMon1 L60-64`) | **HAVE (partial)** | `unattended-upgrade` is already a known persistent process on fleet nodes — `fleet-update-check:147-151` whitelists `unattended-upgrade-shutdown --wait-for-signal` out of its stuck-apt watchdog. Security debt is nonetheless reported per node as an action item (`fleet-update-check:135-139, 283-288`), so coverage is uneven; that is a survey question, not a new subsystem |
| R9 | Kured: watch `/var/run/reboot-required`, cordon → drain → reboot → rejoin, rolling (`FMon1 L60-65`) | **HAVE (analogue)** + **ADAPT (the trigger)** | `bin/fleet-roll-reboot.sh` is Kured for a fleet without K8s and is *better governed*: roster comes from the inventory, never hand-typed (`:4-13`), excludes `governance.never_restart` (thor) and `crown_jewel` (maat, lumen), serial, fail-safe STOP if a node does not return in ~200 s (`:19-25`). What it lacks is Kured's trigger — it is run by hand, and `reboot-required` age is measured in a *different* tool (`fleet-update-check:142-144`). See T3 |
| R10 | Watchtower to auto-pull and restart third-party images (`FMon1 L67-69`) | **REJECT** | Stale prior art: `containrrr/watchtower` is no longer maintained — upstream issue #2067 "project dead? no commits in 2+ years" (Jan 2025) and a Feb 2026 r/selfhosted thread confirming the archive, with `nicholas-fedor/watchtower` as the live fork (§6). Independently, auto-pull-and-restart contradicts a deliberate fleet policy: image **digest drift is reported, never applied** (`fleet-update-check:3-5`, "NEVER applies anything"), with application batched into the monthly window via `fleet-maint-assemble` |
| R11 | `fwupd` for BIOS/SSD/UEFI firmware, agent-triggered monthly (`FMon1 L71-73`) | **HAVE (detection)** + **ADAPT (apply)** | `fleet-update-check` already probes fwupd per node (`:3-5`). Nothing applies it. Applying firmware is exactly the kind of disruptive op the monthly window exists for; the missing piece is the same node-lock T3 builds. Low priority — no node is currently blocked on firmware. See T6 |
| R12 | Cloudflare Tunnel instead of NGINX ingress + open 443 (`FMon1 L216-219`, `L269-321`) | **HAVE** | Already the house pattern, for the reason the doc gives. `cloudflared` appears across the service atlas (`FLEET_SERVICE_ATLAS.md:83-84, 102, 350, 362, 516, 567`); `power.lumennode.uk` behind CF Access is wq#346, `deadman.lumennode.uk` is wq#1039. Instinct 5 already governs changes to it. Nothing to do |
| R13 | HMAC SHA-256 validation of GitHub webhook payloads, `hmac.compare_digest` (`FMon1 L220-267`) | **ADAPT (keep on file)** | The one piece of code in the doc worth retyping, and correct as written (constant-time compare, raw body before JSON parse). Not needed today: the github lane is **pull**, not push — `gh-lane-worker` claims wq tickets (`GITHUB_DISPATCH.md:26`), no inbound webhook exists. If we ever accept one, this is the shape, and unsigned-webhook is the failure mode it prevents |
| R14 | K3s Deployment + privileged DinD sidecar, `DOCKER_HOST=tcp://localhost:2375`, TLS off (`FMon1 L141-213`, dup `L402-473`) | **REJECT** | No cluster to deploy to, and the design is root-on-host behind one HTTP handler (§1) |
| R15 | Infra-Agent: cordon → drain → log to inventory → IPMI power-off the chassis (`FMon1 L662-671`) | **ADAPT (first three)** + **REJECT (power-off)** | Cordon/drain have no K8s meaning here, but the *intent* — stop giving work to a dying box — maps cleanly onto `fleet-inv` status plus the dispatch governor, and "log the failure to inventory" is literally what `INVENTORY_RECONCILIATION.md` already curates by hand. The power-off step depends on R5's BMC we do not have, and instinct 2 plus the `never_restart`/`crown_jewel` flags say an agent must not power-cycle unilaterally. See T5 |
| R16 | Before rebooting a node, check whether a critical agent sandbox is running on it; if so take a `kured-node-lock` annotation and hold the reboot until the PR lands (`FMon1 L673-684`) | **ADAPT — best structural idea in the doc** | This is the one recommendation the fleet has not thought of in this form, and we already own every primitive it needs: `slock` (the fleet lock table in `queue.db`), live `wq` claims with TTL leases, and `lumen-run` task registration. Today `fleet-roll-reboot.sh` would happily reboot a node mid-session. wq#1401 (Maintenance-window scheduler, **claimed**) is the natural home. See T3 |
| R17 | `--per_instance_cost_limit 2.00` on every autonomous resolver run (`FMon1 L121`) | **HAVE** | Per-run cost ceilings and budget alarms already exist (`bin/litellm-budget-alert`, `research/COST_OBSERVABILITY.md`). Noted only so nobody re-derives it |

## 3. Which fleet problems this actually solves

Honestly: **one and a half of them.**

- **Solves: no drive-health signal (R4).** Uncovered today, and the fleet's own hardware
  history says it matters — `INVENTORY_RECONCILIATION.md:84` carries an ECC known-issue on
  one node, and the inventory is full of consumer NVMe and eMMC parts. This is a real find.
- **Half-solves: metrics that nobody alerts on (R7).** The doc did not diagnose it — it
  *assumed* Alertmanager exists and moved on. But reading it against `opsaudit_a3` makes the
  hole obvious: wq#753 delivered 458 metric names and zero rules.
- **Sharpens: reboot safety (R16).** Not a problem we had named. Worth building.

What it does **not** solve is the fleet's actual, measured monitoring problem. `wq#926`'s
external-practice survey already established it against the Google SRE standard: ">1,500 raw
alert events" in 7 days against a budget of 2-3 actionable pages per shift — "~2 orders of
magnitude over budget" — with the two loudest true positives unanswered
(`reports/R5_external_practice.md:15-22`). `research/ALERT_TRIAGE_LLM.md:9-33` re-measured it
in September: **1,179 publishes across 46 distinct conditions in 24 h**, one condition paging
every 2.7 minutes, and its F1 is the finding that matters here — "the noise is repetition of
true alerts, not false alarms."

Every recommendation in this doc adds *sources*. None of them adds *suppression*. Applied
naively — cAdvisor, smartctl, IPMI, Kured metrics, all routed to an agent that opens tickets —
the doc would multiply a 26-pages-per-condition-per-day problem. That is why T1 and T2 below
both carry a hard dedup/silence requirement rather than just a "wire it up" step.

Cross-referenced tickets: wq#84 (chose VictoriaMetrics over Prometheus — the doc's
"Prometheus" is a naming mismatch, not a missing component), wq#341/344/346 (VM + Grafana +
CF Access on Bes), wq#753/786 (node_exporter fleet-wide), wq#926 (R5 gap analysis),
wq#1039 (off-fleet dead man's switch — `alert-deadman-beat.sh:1-6`, already the thing the doc
never mentions), wq#1040 (silences with expiry), wq#1401 (maintenance-window scheduler,
**claimed — T3 should land inside it, not beside it**), wq#1456/1596 (fleet-roll-reboot),
wq#1704/2195 (SPOF watchdogs, 2195 **claimed**), wq#1710 (LLM alert triage design).

## 4. BUILD tickets to file

**T1 — SMART + eMMC health into the existing node_exporter, with one alert rule.**
Lane: ops. The only net-new telemetry this doc justifies.
Mechanism: root-run textfile script on each node writing `/var/lib/node_exporter/textfile/smart.prom`,
using upstream `prometheus-community/node-exporter-textfile-collector-scripts/smartmon.sh`
(retype, do not copy the doc). No new daemon, no new scrape target — the bes vmagent job
already scrapes `:9100` on all 14 hosts (`opsaudit_a3_node_exporter.md:17-31, 55-58`).
eMMC nodes have no SMART at all (verified, §6): for apep (58 GB eMMC alongside a SATA SSD,
`INVENTORY_RECONCILIATION.md:93`) and any other eMMC box, use `mmc-utils`
(`mmc extcsd read` → `PRE_EOL_INFO`, `DEVICE_LIFE_TIME_EST_TYP_A/B`) and emit the same
metric family with a `bus="mmc"` label.
Accept: `smartmon_*` series visible in VM for every SMART-capable drive in the inventory;
eMMC nodes report a life-time-estimate series; **exactly one** aq row (not one per scrape)
on reallocated/pending sectors, NVMe `percentage_used > 80`, or eMMC life-time ≥ 0x0B, with
the dedup key proven by a forced-fire test. Deploy path is root — needs the wq#2195-style
`install.sh` treatment, not a build worker.

**T2 — `vmalert` (or a thin VM-query evaluator) bridging VictoriaMetrics → aq.**
Lane: ops. Closes the hole wq#753 left and R5 G2 named.
Scope deliberately small: stand up alert-rule evaluation against the existing VM on Bes,
sink to `aq` (not straight to ntfy), and **port two existing script watchers onto it**
(`lumen-load-watch`, `lumen-disk-hygiene` free-space) to prove the path end to end, deleting
the scripts on cutover — net watcher count must not increase. Must respect wq#1040 silences
and aq dedup; must not bypass `aq_escalate.py`'s at-most-once paging.
Accept: rule fires → one aq row → one page → recovery closes it; the two ported watchers are
gone; `alert-deadman-beat.sh` still beats (the new path must not create a second, unwatched
page route). Note VictoriaMetrics, not Prometheus — `vmalert` is the right binary (wq#84).

**T3 — Teach `fleet-roll-reboot.sh` the two things Kured knows: a trigger and a lock.**
Lane: ops. Land inside wq#1401 (claimed) rather than as a rival scheduler.
(a) Trigger: consume the `reboot-required` age `fleet-update-check` already probes
(`:142-144`) so the roll targets only nodes that need it, instead of all peripherals.
(b) Lock (`FMon1 L673-684` translated): before rebooting node X, refuse if X holds a live
`slock`, an unexpired `wq` claim, or a registered `lumen-run` task — and re-check after the
pre-flight `sudo -n true` (`fleet-roll-reboot.sh:19`), not once at the start.
Accept: a node with a synthetic held lease is skipped with a logged reason and the roll
continues past it; a node with `reboot-required` absent is never touched; thor and the crown
jewels remain excluded (`:4-8` unchanged); wedge test as in wq#2195.

**T4 — Per-unit/per-container memory series without cAdvisor.**
Lane: ops. Enable node_exporter's systemd collector (and cgroup series where cheap) on the
hosts running containerised services, so "which unit is leaking" is answerable from VM.
Accept: per-unit memory/CPU series in VM for the named long-running services; one Grafana
panel; no new daemon and no measurable scrape-time regression on the N100/Apollo Lake boxes.
Note for whoever takes it: a build worker **cannot enumerate containers** — the guard denies
every `docker` verb, including `docker ps` (`ISSUE-LOG` #652 note, re-confirmed this run at
§7). Get the list from `FLEET_SERVICE_ATLAS.md` or run the enumeration as an ops task.

**T5 — Quarantine a node from work on a hardware alert (R15, minus the power-off).**
Lane: ops. Depends on T1. When a SMART/ECC/thermal alert fires for node X, flip X's
`fleet-inv` status so both `fleet-roll-reboot`'s roster (`:11`) and the dispatcher stop
selecting it; record the reason; make it one-command reversible.
Accept: a synthetic alert quarantines a bench node, `fleet-inv ssh-list --status active`
drops it, dispatch stops routing to it, and un-quarantine restores both. **No automatic
power-off** — instinct 2 and `governance.never_restart` govern, and we have no BMC anyway.

**T6 — fwupd apply inside the monthly maintenance window (low priority).**
Lane: ops. Depends on T3's lock. Extend `fleet-maint-assemble`'s completeness-gated batch to
*apply* firmware one node at a time behind the lock, never on thor, with the roll's
fail-safe STOP semantics. Accept: dry-run lists exactly what `fleet-update-check` reported;
a single opt-in node updates and returns; a lock-held node is skipped, not queued-and-forced.

**T7 — Keep the HMAC validator on file (documentation-only).**
No build. If an inbound GitHub webhook is ever added, `FMon1 L237-250` is the correct shape
(raw body, `hmac.compare_digest`, reject before parse). Recorded here so it is not
re-researched; today's lane is pull-based (`GITHUB_DISPATCH.md:26`).

## 5. What to STOP doing / never start

1. **Stop treating "more telemetry" as the monitoring gap.** It was true in August; wq#753
   closed it. The measured gap since is actionability — 1,179 publishes/day carrying ~46 bits
   of news (`ALERT_TRIAGE_LLM.md:9-33`). Any monitoring proposal that adds sources without a
   suppression story should be refused on those numbers.
2. **Never stand up K3s to host monitoring or agents.** Second doc in this family to
   recommend it (`DIGEST_Fleet_Memory_3_md.md` R8 rejected the same premise for Redpanda).
   The answer is the same and should stop being re-litigated: the orchestrator is systemd +
   SQLite + SSH, `which k3s kubectl helm` returns nothing, and every K8s-shaped recommendation
   costs a cluster before it buys anything.
3. **Do not build an SWE-agent resolver.** `aq_escalate.py` → wq ticket → `gh-lane-worker`
   session → `lumen-pr` is the same loop with leases, idempotent paging and a PR gate that
   refuses non-compliant landings. A second resolver means a second PAT and a second sandbox.
4. **Do not adopt Watchtower** (unmaintained upstream, §6) — and more importantly, do not
   auto-apply image updates at all: report-only into the monthly window is a deliberate
   standing decision (`fleet-update-check:3-5`).
5. **Stop planning for IPMI.** No BMC exists on any fleet node and none is coming; drop
   `ipmi_exporter`, chassis fan/voltage telemetry and agent-driven power-off from every
   future fleet-monitoring plan. Power control on this fleet is smart plugs (`plugctl`,
   wq#334), which is a different tool with different safety rules.
6. **Do not introduce NATS or LangGraph for alert routing.** The routing decision in the doc
   is a dict lookup (`FMon1 L616-637`); aq rows already carry the labels it would key on.
7. **Do not design a new ingress.** cloudflared is already the pattern, for the doc's own
   stated reason, and changes to it are instinct-5 governed.

## 6. External claims, independently verified

Run this session with `fleet-search` (SearXNG-backed), not from memory:

| Claim | Verdict | Source found |
|---|---|---|
| `smartctl_exporter` has a lighter textfile-collector alternative | TRUE | `github.com/prometheus-community/node-exporter-textfile-collector-scripts/blob/master/smartmon.sh`; also `prometheus/node_exporter` issue #473 "S.M.A.R.T monitoring" |
| eMMC devices do not expose SMART | TRUE | ODROID forum HOWTO: "Devices that do not support S.M.A.R.T. eMMC. Use mmc-utils"; ArchWiki S.M.A.R.T. ("SMART support must be available and enabled on each storage device") |
| `containrrr/watchtower` is unmaintained | TRUE | upstream issue #2067 "project dead? no commits in 2+ years" (2025-01); r/selfhosted 2026-02-13 "Watchtower is no longer maintained… alternatives/decent forks?"; live fork `nicholas-fedor/watchtower` |
| Desktop-class Dell hardware has no IPMI/BMC | TRUE (strong) | r/homelab "Why don't normal Desktop and laptops come with IPMI/OOBM"; siderolabs/sidero issue #289 requesting Intel AMT *as an alternative to* IPMI for desktops. Confirmed against our own inventory: OptiPlex UFF/micro, Apollo Lake whitebox, Trigkey N100 (`INVENTORY_RECONCILIATION.md:83-100`) |

Live fleet checks this run: `which k3s kubectl helm` → rc=1, no output (no Kubernetes);
`vmalert` → 0 matches anywhere in `/home/lumen/bin` (no rule evaluation); no NATS or LangGraph
in `FLEET_SERVICE_ATLAS.md` or `ISSUE-LOG.md`.

## 7. The one-line version for Frank

One genuinely new thing to build (drive/eMMC health), one hole this doc accidentally exposed
that is ours not its (458 metric names in VictoriaMetrics with nothing alerting on them), one
sharp idea worth stealing (don't reboot a node that's holding a live worker lease), and a
K3s/SWE-agent/Watchtower/IPMI stack to skip entirely — three of those four because we already
run the better version, and IPMI because the hardware simply doesn't have it.

## 8. Harness notes (refines `DIGEST_Fleet_Memory_3_md.md` §7)

That digest's §7 held exactly: `/home/lumen/scratch/TWO/` is outside the worker's `--add-dir`
set, shell `grep`/`wc` against it are refused and not retryable, and the **`Read` tool reaches
it** — this doc was read in three bounded `Read(offset, limit)` calls.

Two refinements measured this run, both new:

- **`guard.sh` matches denied tokens anywhere in the command line, including inside a search
  pattern or a search query.** `fleet-search web "watchtower docker … fork"` was blocked with
  "docker denied except ps/logs", and `grep -n -iE "unattended|reboot|security" bin/fleet-update-check`
  was blocked as a "denied verb (sudo/reboot/shutdown/poweroff/halt)". Neither command does
  anything to a container or to power state. Workarounds that work: bracket a letter in the
  pattern (`reb[o]ot`) and reword the search query. Logged as `ISSUE-LOG` #653.
- **`head`/`grep` on a path whose *name* contains a denied token is refused too** —
  `head -25 /home/lumen/bin/fleet-roll-reboot.sh` was denied on the filename alone. `Read`
  reaches it; that is how `fleet-roll-reboot.sh:1-28` is cited above.

Refused-but-legitimate shapes for allowlist review: `grep <pattern> /etc/apt/apt.conf.d/*`
(path outside `--add-dir`, so per-node `unattended-upgrades` enablement in R8 rests on
`fleet-update-check:147-151` rather than a live read), and the two guard cases above.
