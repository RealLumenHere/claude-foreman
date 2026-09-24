# DIGEST — Frank's "Fleet Monitoring 2.md" (wq#2467)

Source: `/home/lumen/scratch/TWO/Fleet Monitoring 2.md`, 1286 lines, read in full 2026-09-24.
The doc restarts its own numbering seven times ("## **1\.**" appears seven times), so every
citation is a **line range** plus heading text, e.g. `FM2 L61-110 §"Setting Up smartctl_exporter"`.

Fifth of the wq#2463-2469 digest family and the direct continuation of `Fleet monitoring 1.md`.
`DIGEST_Fleet_monitoring_1_md.md:19-23` predicted this file: FMon1 ends mid-sentence promising
"Kured's reboot schedule via Kubernetes annotations and deploying the `smartctl_exporter`", and
**this doc opens with exactly those two sections** (`FM2 L1-151`). Read that digest first — 6 of
its 17 recommendations reappear here and are already adjudicated; this digest does not re-argue
them, it only records what the second doc adds.

## 1. What the doc is, and how much of it to trust

A raw multi-turn chat transcript, same provenance as the rest of the family. Four properties
matter before acting on any line:

- **No duplicate chapters.** Unlike FMon1 (half the file) and Orchestation 2 (two repeated
  chapters), this file is unique end to end. All 1286 lines are content.
- **Same escape-mangling.** `mcp \= FastMCP(...)`, `\-\>`, `\# comment`, `node\_info\["type"\]`,
  `\---` between YAML documents. Nothing here is pasteable; anything we implement gets retyped
  from upstream docs, not copied.
- **Eight of its ~11 code blocks are FastMCP servers wrapping one shell command.** `amttool`,
  `openscad`/`prusa-slicer-cli`, a Home Assistant `POST /services/switch/turn_on`, two
  `v1.patch_node` calls. The doc's reflex is "wrap it in an MCP server"; the actual content of
  each is two lines of subprocess or requests. Judge the capability, not the wrapper.
- **It ends abruptly at `FM2 L1286`** mid-thought after the FastAPI JWT dependency, with no
  closing section. Expect a continuation in `Fleet up to 11.md` (wq#2469, still open).

Four things in it are actively dangerous and must not be copied even as a pattern:

1. **Credentials inline in code and manifests**, four times: an Intel AMT admin password in a
   Python dict (`FM2 L177-180`), a Tapo password beside it, `TUNNEL_TOKEN: "YOUR_CLOUDFLARE_TOKEN"`
   as a literal env value in a Deployment (`FM2 L1044-1046`), `neo4j`/`password` (`FM2 L717`) and
   `minioadmin`/`minioadmin` (`FM2 L770-772`). Same class as FMon1's plaintext `ghp_…`; instinct 4.
2. **Shell injection with a secret on the command line**: `subprocess.run(f"amttool {ip} reset
   {pass}", shell=True)` (`FM2 L193-194`) and `os.system(f"openscad -o /tmp/{filename}.stl ...")`
   (`FM2 L523-525`). Both interpolate agent-controlled strings into a shell, and the first puts a
   password into a process's argv where any `ps` prints it — the exact shape instinct 4 forbids.
3. **A privileged DaemonSet with `hostNetwork: true` and `/dev` mounted** (`FM2 L86-108`), i.e.
   root on every host in the fleet, to read SMART counters that a per-node textfile script reads
   unprivileged-with-one-sudoers-line.
4. **An agent that deletes its own memory** (`FM2 L741-745`, `DETACH DELETE` of raw episodic
   nodes on a weekly timer). Instinct 7 forbids this outright: memory consolidation is
   proposal-only, nothing auto-edits the memory stores.

One technical error worth naming because it changes the design we steal: the doc claims that
annotating a **node** with `weave.works/kured-node-lock` makes Kured skip it (`FM2 L5-7`,
`L26-39`). Upstream says that annotation is where Kured "record[s] locking node" — a cluster-wide
mutex held on the DaemonSet so only one machine restarts at a time (verified, §6). Blocking one
specific machine is a *different* mechanism (blocking-pod selector / alert-based block). The idea
is still the best one in the family, but the primitive we want is "block this one node while it
holds work", not "hold the global mutex".

## 2. Every actionable recommendation, with a fleet verdict

Verdicts: **HAVE** = already built and verified; **ADAPT** = right idea, wrong mechanism for a
fleet with no Kubernetes; **REJECT** = do not build; **NEW** = genuine uncovered gap.

The premise is false for us for the third document running: **there is no Kubernetes on this
fleet.** `which k3s kubectl helm longhorn velero linkerd argocd` → rc=1, no output, re-verified
live this run. Orchestration is systemd units (300 fleet-authored), SQLite (`queue.db`,
`minerva.db`, `alerts.db`), SSH fan-out over a tailnet, and ~35 containers.

| # | Recommendation (citation) | Verdict | Evidence |
|---|---|---|---|
| R1 | Kured node lock: take `weave.works/kured-node-lock` before a heavy task, release after (`FM2 L1-59`) | **ADAPT — the idea to steal** | Already scoped as FMon1 T3 (`DIGEST_Fleet_monitoring_1_md.md:129-138`), still unfiled. This doc adds the *lease* framing (lock is taken by the orchestrator before dispatch, not by the node), which is the right shape for us: `slock`, live `wq` claims with TTL leases and `lumen-run` task registration are the three primitives, and `fleet-roll-reboot.sh` currently consults none of them. Correct the mechanism per §1 — per-node block, not global mutex |
| R2 | `smartctl_exporter` as a privileged DaemonSet (`FM2 L61-110`) | **ADAPT** | Confirmed gap (no SMART collection in `bin/`), already scoped as FMon1 T1 via the node_exporter **textfile collector** — no new daemon, no new scrape target, no `privileged: true`. This doc adds nothing to that design except the privileged shape we are rejecting |
| R3 | ServiceMonitor → Prometheus → Alertmanager rules on `smartctl_device_*` (`FM2 L112-151`) | **NEW — restates the real hole** | The fleet has **three** telemetry sinks and **zero** rule evaluation over any of them: 458 node_exporter metric names in VictoriaMetrics (`reports/opsaudit_a3_node_exporter.md`), Claude-Code OTel metrics pushed to VM's OTLP endpoint (`harness/fworker.py:498, 2094-2098`), and Langfuse traces (`harness/fworker.py:135-138`). `vmalert` → 0 matches in `/home/lumen/bin`. FMon1 T2 named it; this doc is the second independent hit on the same hole |
| R4 | Hardware Control MCP: Intel AMT `amttool <ip> reset` for out-of-band power cycling (`FM2 L157-197`) | **REJECT** | FMon1 R5 rejected IPMI for the same reason and it holds for AMT: the inventory is OptiPlex UFF/micro, Apollo Lake whiteboxes, Trigkey N100 and Macs — vPro/AMT is a per-SKU option nobody verified on any of them, and no node's state record carries an AMT fact. Plus the injection + argv-password defect in §1 |
| R5 | Tapo P115 smart plugs over local KLAP as the OOB path for nodes with no BMC (`FM2 L199-216`) | **HAVE** | This is the fleet's power layer already and has been since wq#334: 27 plug tools in `bin/` (`plugctl`, `plug-ctl`, `plug-recover`, `power-remediate`, `plug-baseline`, `plug-delta`, `plug-findings`), `lumen-plug-snapshot.timer` every 60 s feeding Glance (`FLEET_SERVICE_ATLAS.md:315`), Tapo-direct control from Bes plus an HA-mediated path (`:88`). Note the doc's own inconsistency: it proposes an agent that power-cycles machines at will, which `governance.never_restart` (thor) and `crown_jewel` (maat, lumen) forbid |
| R6 | Discovery Agent: CronJob every 6 h scraping HN RSS / GitHub trending / HF Daily Papers → queue (`FM2 L218-291`) | **HAVE (partial) + ALREADY FILED — one narrow gap left** | Do not build this from scratch; almost all of it exists. `github_scout` (`lumen_scout_graph.py`) already queues candidate repos, Miniflux already runs on Sia (`bin/sia-news-pull:6`), and the measured defect — a star **floor** plus a star **sort** cannot surface a two-week-old project, median 36,860 stars across its queue (wq#1717) — is exactly what wq#2500's RSSHub velocity bridge addresses (`DIGEST_Orchestation_2_md.md:198, 214`; ops bundle shipped, live install needs an on-host operator, `kb/ISSUE-LOG.md` #662). The narrow thing this doc adds that the fleet genuinely does not sample: **non-GitHub sources** — HN, Hugging Face daily papers, `r/selfhosted`/`r/LocalLLaMA`. The other watchers are all narrow by design: one GitHub user, one YouTube channel, BC-250 keywords, eBay listings (`FLEET_SERVICE_ATLAS.md:161, 289, 292, 381`). See T1 |
| R7 | Reflexion: generate → execute in sandbox → critique the failure → regenerate, bounded by `max_retries` (`FM2 L299-382`) | **HAVE — landed the same day** | `harness/fworker.py:188-194` runs a critique pass between attempt 1 and 2 (wq#2488, commit `0a7f97d`, 2026-09-24), `MAX_ATTEMPTS = 2` (`:423`) is the bound the doc's `routing_logic` provides, and `loop_break_classification` (`:475-486`, wq#1107) is strictly better than the doc's counter: it distinguishes "the model is repeating a failed approach" from "the harness refused the call", so a settings bug cannot spend the dispatcher's circuit-breaker budget (as it did on 2026-08-29, 8/9 attempts). 76 tests in `harness/tests/test_prior_attempt_critique.py` |
| R8 | Distil the supervisor into a local Llama-3-8B with Unsloth LoRA on an RTX 4060, then route `claude-supervisor` to `ollama/…` (`FM2 L384-442`) | **REJECT — no such hardware, by decision** | There is no NVIDIA GPU anywhere on this fleet. The GPUs that exist are an AMD Lexa PRO under a standing "video output only" directive and two pairs of Mac Pro D300/D700 Curacao XT cards that were **deliberately retired from compute** (`INVENTORY_RECONCILIATION.md:77, 116, 126, 245, 269`; wq#2136/2157 `disable-gpus`, verified in `ops/tests/test_wq2136_disable_gpus_verify.py`). Qwen3-32B runs on CPU. The fleet's local-model answer is *model selection*, not fine-tuning: `LOCAL_MODEL_STRATEGY.md:13-22` picks Qwen3.6-35B-A3B and Gemma-4-26B-A4B MoE quants per rung on named CPU hosts |
| R9 | Bind every node into a Tailscale WireGuard mesh before installing K3s (`FM2 L444-458`) | **HAVE** | The fleet *is* a tailnet and has been since before the doc was written — every address in this session is 100.x, the node roster is derived from tailnet membership (`kb/ISSUE-LOG.md:12`), and version management is tracked (wq#1510 fleet-wide v1.102.3, wq#2496 UDP GRO/GSO measurement). The K3s-specific half (`--flannel-iface=tailscale0`) has no meaning here |
| R10 | OpenTelemetry Collector + LangChain callbacks → Jaeger/Grafana for agent traces (`FM2 L460-489`) | **HAVE, twice** | Langfuse is deployed and load-bearing (`langfuse-evaluator`, `langfuse-idle-alert`, `langfuse-evaluator-smoke`, PG+ClickHouse both in the hourly/daily backup sets — `FLEET_SERVICE_ATLAS.md:33, 76, 120-121, 159, 251`), and fworker emits **one trace per ticket run with every attempt as a span** (`harness/fworker.py:135-138`, wq#754). Separately, OTLP metrics from the Claude CLI already push to VictoriaMetrics (`:498, 2094-2098`). Adding an OTel Collector + Jaeger would be a third trace store |
| R11 | Fabrication MCP: OpenSCAD → STL → G-code → Klipper/Moonraker on a Wyse 3040 → Ender 3 (`FM2 L495-544`) | **PARK (blocked on a physical fact)** | Not fantasy — there is a `3dprinter` node in the inventory with nine klipper/ender/prusa facts and a tailscale IP. It has been **offline on the tailnet since 2026-07-26** while inventory still says `power: on`, and everything beyond the model name is unrecorded (grade D, `INVENTORY_RECONCILIATION.md:196`). Reconciling that node is a prerequisite to any of this, and it is a physical task, not a build. The `os.system` f-string in the doc is not the implementation |
| R12 | Longhorn distributed block storage, 3× replication, default StorageClass (`FM2 L546-581`) | **REJECT** | No cluster. Storage is md0 RAID5 `/data/lumen` + md1 RAID0 scratch, assembled and asserted at boot against a manifest (`FLEET_SERVICE_ATLAS.md:100-101, 112`, wq#421). Replication across mini-PCs over a tailnet would cost more than the arrays it replaces |
| R13 | External Secrets Operator + HashiCorp Vault for static API keys (`FM2 L583-631`) | **ADAPT (already solved differently)** | ESO is a Kubernetes operator; the fleet's vault is Vaultwarden with a real HA story — `wadjet-ha-monitor` probes Ma'at every 30 s and auto-promotes, `vw-bes-datasync`/`vw-sobek-datasync` replicate hourly, `lumen-backup-critical` dumps it hourly (`FLEET_SERVICE_ATLAS.md:90, 120, 125-127`). Agent-side, instinct 4 plus `lumen-secret-check`, `mask-secrets` and `read_dir_sweep` are the enforcement |
| R14 | Linkerd service mesh for pod-to-pod mTLS (`FM2 L637-654`) | **REJECT** | No pod network to mesh. The trust boundary is the tailnet (already WireGuard-encrypted, R9) plus `iptables-restore` at boot (`FLEET_SERVICE_ATLAS.md:105`). A sidecar per service on N100/Apollo Lake hardware buys re-encryption of already-encrypted traffic |
| R15 | PriorityClasses so orchestration always outranks disposable workers (`FM2 L656-699`) | **ADAPT (analogue exists; its known defect is the real version of this)** | wq priorities p0-p4, the dispatch governor and subsystem holds already express "who wins" at dispatch time. What the doc is groping for at the *node* level is exactly the fleet's own named finding: **three uncoordinated RAM actuators** — node-killer (continuous), rosco (hourly), self-healer (RAM crit 90 %) — with no shared cooldown and no name-allowlist for `fworker`/`lumen-run` wrappers, so "a healthy 7-day-old fworker wrapper [gets] reaped by rosco" (`FLEET_SERVICE_ATLAS.md:517`, §5 item 9). That is a filed-quality problem the doc independently rediscovered |
| R16 | Weekly memory compaction: summarise old failure nodes into one heuristic, `DETACH DELETE` the raw ones (`FM2 L701-750`) | **HAVE (the intent) + REJECT (the deletion)** | Consolidation already exists as a pipeline: minerva classifier → proposal → eval, `dream`/`reflector`, `lumen-familiarity`, `embed-guard`, `recall-bench` (`FLEET_SERVICE_ATLAS.md:80`). The deletion step is forbidden by instinct 7 — candidates go to Minerva for Frank's review, nothing auto-edits `claude_memory.md` or `instincts.yaml`. The doc's design would silently destroy the raw record that every later post-mortem needs |
| R17 | Velero + MinIO for cluster DR, daily schedule, CSI volume snapshots (`FM2 L758-794`) | **HAVE (better)** | A five-unit restic chain: `lumen-backup-critical` hourly (Vaultwarden, Langfuse PG, pgvector, Redis, agent SQLite), `lumen-backup-important` daily (Langfuse CH, MinIO, Plausible, MongoDB), `lumen-backup-offsite` daily restic copy to Thoth, weekly prune, **monthly `restic check` integrity verify** (`FLEET_SERVICE_ATLAS.md:120-124`). MinIO is not the backup target here — it is one of the things backed up. The doc has no verify step; we do |
| R18 | Kubernetes RBAC: a ServiceAccount whose Role cannot read Secrets (`FM2 L796-845`) | **HAVE (stronger, and enforced per call)** | The worker sandbox is least-privilege by construction: a per-command Bash allowlist (`harness/worker-settings.json`), a filesystem bound to the worktree plus explicit `--add-dir` roots, and a PreToolUse guard hook denying credential reads and destructive verbs. Unlike RBAC this is enforced at every tool call, not at an API boundary — this session produced four refusals while writing this digest (§8) |
| R19 | Open WebUI as the conversational interface to LiteLLM (`FM2 L847-855`, `L1054-1059`) | **HAVE** | Multiple interfaces already ride the one tunnel: `chat`, `claude`, `ask`, `propose`, `mctl`, `scribe`, `bench`, `law`, `glance` (`FLEET_SERVICE_ATLAS.md:84`), plus LibreChat and Dify in the container set (`:34`), `lumen-messenger` on :8765, and `lumen-command-receiver` taking commands from the phone over ntfy |
| R20 | Vault dynamic PostgreSQL secrets: per-pod 1 h credentials that self-destruct (`FM2 L863-921`) | **ADAPT (low priority, honest gap)** | Genuinely absent — fleet credentials are long-lived secrets in 0600 files behind git credential helpers, rotated by hand-run tools (`github-pat-rotate`, `rotate-gitea-cred`, `rotate-fleet-pw`, `vw-migrate-secrets`). The doc's answer requires standing up HashiCorp Vault beside Vaultwarden for one database. The cheaper 80 %: wq#2271/2272 (token-engine credential helper + pre-push hook refusing userinfo URLs) are already open and address the same exposure |
| R21 | Export agent-cognition metrics (`agent_reflexion_loops_total`), alert on `rate(...[5m]) > 10`, pause the thread (`FM2 L923-950`) | **HAVE (the bound) + NEW (the signal)** | The per-run bound is better than the doc's: `MAX_ATTEMPTS = 2` plus a loop breaker that classifies permission-wall trips separately (`harness/fworker.py:423, 475-486`). What does not exist is the *aggregate*: nothing emits or alerts on attempt-2 rate, classification mix, breaker trips or `PERMISSION_LOOP` counts, so 2026-08-29's "8/9 attempts, breaker at 89 %, DISPATCH_DISARM set" was discovered after the fact. The data is already in `queue.db` and Langfuse; only the evaluator is missing. See T2 |
| R22 | Home Assistant MCP: thermals spike → switch on rack fans via Tapo plugs (`FM2 L952-1003`) | **ALREADY FILED — extend wq#2484** | Every piece exists: Home Assistant runs on Bes with MQTT (`FLEET_SERVICE_ATLAS.md:88`), HA-mediated plug control is a named path, and `lumen-guardian` already performs emergency thermal shutdown (`:513`). wq#2484 (open, p3, from `DIGEST_Fleet_searchability_md.md` T4) is the thermal-backpressure ticket and explicitly says "do not add a new collector". Actuating a fan is a second, cheaper rung of the same ladder — add it there, do not file a rival |
| R23 | Cloudflare Tunnel instead of open ports; `cloudflared` Deployment with the token inline (`FM2 L1011-1048`) | **HAVE** | One tunnel serves every `*.lumennode.uk` (`FLEET_SERVICE_ATLAS.md:84, 102`); instinct 5 governs changes to it and the SIGKILL-mid-script hazard is documented. The inline `TUNNEL_TOKEN` is the §1 credential defect, not a pattern |
| R24 | Linkerd Viz / Cilium Hubble for a live service topology map (`FM2 L1061-1066`) | **REJECT (the tool) — the need is real and already answered by hand** | No pod mesh to observe. But the underlying question — what breaks when X dies — is a genuine fleet problem and `FLEET_SERVICE_ATLAS.md:14` states why: only 4 services carry a hard `Requires=`, so "systemd cannot tell you what breaks when X dies"; the real graph lives in TCP ports, SQLite files and containers, and §4 of that atlas maps it manually. A mesh dashboard would not have found any of those edges |
| R25 | ArgoCD UI for GitOps rollback of agent-authored deployments (`FM2 L1068-1073`) | **ADAPT (analogue exists)** | Gitea + `lumen-pr` (which "refuses the non-compliant one") is the landing path, and `unit-drift` snapshot/apply is the systemd-side declarative baseline. Rollback is `git revert` + `unit-drift apply`. What we lack versus ArgoCD is continuous drift *detection* on applied state — which `unit-drift` does for unit files and `drift-assert` does for a GOLDEN set |
| R26 | A Homepage/Homer portal so you do not memorise subdomains (`FM2 L1075-1102`) | **HAVE** | `glance` is deployed as a container and served at `glance.lumennode.uk` (`FLEET_SERVICE_ATLAS.md:34, 84`), fed by `lumen-plug-snapshot` every 60 s (`:315`) |
| R27 | Cloudflare Access with GitHub OAuth as the identity gate (`FM2 L1104-1153`) | **HAVE** | CF Access already fronts the public surface (`power.lumennode.uk` wq#346, `deadman.lumennode.uk` wq#1039). The step-by-step OAuth-app setup is correct but describes a thing already done |
| R28 | Verify the `Cf-Access-Jwt-Assertion` JWT against the team JWKS instead of trusting `Cf-Access-Authenticated-User-Email` (`FM2 L1155-1284`) | **NEW (small, real)** | The one piece of code in this doc worth retyping — the same role FMon1's HMAC validator played. Claim verified live against Cloudflare's own docs (§6). Nothing in the fleet is known to verify this header today; every origin behind the tunnel trusts the tunnel. Two defects to fix while retyping: the doc fetches JWKS on **every request** (an outbound HTTPS call inside the auth path, and a hard dependency on CF reachability — cache with a TTL), and it never checks `iss`. See T3 |

## 3. Which fleet problems this actually solves

**Two, plus a meta-finding that is worth more than either.**

- **Solves: nothing evaluates the telemetry (R3, R21).** Three sinks, zero rules. FMon1 found
  this against node_exporter; this doc finds it again from a different direction (agent
  cognition) and adds the observation that the *agent* side has the same hole as the *hardware*
  side. Both halves want the same missing component.
- **Narrows: inbound discovery samples one axis (R6).** `github_scout` + Miniflux + wq#2500's
  velocity bridge already cover GitHub; nothing samples HN, HF papers or the subreddits.
- **Sharpens: don't restart a node that is holding work (R1)** — already FMon1 T3, with the
  mechanism corrected (§1).

**The meta-finding, corrected.** This ticket's framing is "prior art the fleet should have found
itself", and the tempting conclusion is that we have no discovery. That is false and worth
recording so the next digest does not re-derive it: `fleet-search` makes prior-art search the
mandatory *pull* step on any RE/tool-build ticket (wq#991, wq#999), and `github_scout` + Miniflux
are the *push* side. The failure is narrower and already measured — the push side ranks by star
count, so it surfaces `litellm`, `gitea` and `open-webui`, tools we already run, and structurally
cannot surface a two-week-old project (wq#1717, `DIGEST_Orchestation_2_md.md:198`). Frank's
searches beat ours on *recency and breadth of source*, not on existence. That is a tuning problem
with a filed ticket (wq#2500) and one uncovered axis (T1), not a missing subsystem.

What this doc does **not** solve is, again, alert actionability. `reports/R5_external_practice.md`
measured ">1,500 raw alert events" in 7 days against a budget of 2-3 actionable pages per shift;
`research/ALERT_TRIAGE_LLM.md` re-measured 1,179 publishes across 46 conditions in 24 h.
`FLEET_SERVICE_ATLAS.md:510` records the downstream state: **250 open aq rows** (84 ntfy, 78
jay-watch, 37 failed-unit), i.e. "a pile, not a queue". Every recommendation here adds sources
(SMART, thermals, agent loops, discovery). None adds suppression. T1 and T2 therefore both carry
hard budget and dedup requirements rather than a "wire it up" step.

Cross-referenced tickets: wq#334 (plug control), wq#346/1039 (CF Access surfaces), wq#421
(storage assert), wq#753/786 (node_exporter fleet-wide), wq#754 (Langfuse trace-per-run),
wq#926/1040 (alert-budget gap, silences with expiry), wq#1107 (permission-loop classification),
wq#1717 (`github_scout` is a popularity feed), wq#2500 (RSSHub velocity bridge — **R6/T1 build on
this**),
wq#1401 (maintenance-window scheduler, **claimed** — R1/FMon1 T3 lands inside it), wq#1510/2496
(Tailscale), wq#1709 (local model strategy), wq#2136/2157 (GPUs retired), wq#2271/2272
(token-engine credential helper), wq#2484 (thermal backpressure — **R22 extends this**), wq#2488
(reflexion critique, landed), wq#2469 (`Fleet up to 11.md`, the likely continuation of this doc).

## 4. BUILD tickets to file

**T1 — Add the non-GitHub discovery axis to the feed path we already own.**
Lane: research. From `FM2 L241-245`, and deliberately scoped *around* wq#2500 rather than over it.
Do **not** build a new watcher or a new bus. wq#2500 already ships an ops bundle that puts RSSHub
in front of Miniflux on Sia for GitHub-trending velocity; this ticket adds routes on the same
bridge for the sources nothing on the fleet samples: HN (`hnrss.org`, filtered), Hugging Face
daily papers, `r/selfhosted` and `r/LocalLLaMA`. Subscribing Miniflux routes needs only its HTTP
API, not root — `miniflux_subscribe.py` in that bundle is the precedent that a worker *can* run
this half unattended (`kb/ISSUE-LOG.md` #662).
The hard part is not fetching, it is **not flooding a queue that is already 250 aq rows deep**
(`FLEET_SERVICE_ATLAS.md:510`): a hard cap per run (start at 3 items), dedup against seen URLs,
and a relevance gate keyed on the fleet's own open problems rather than generic "AI".
Accept: the new routes are subscribed and returning entries; one run promotes ≤3 items with the
source URL and why each matched; a second run over the same feed promotes **zero**; the cap is
proven against a stubbed 50-entry feed; no ntfy page is emitted (this is a queue filler, not an
alert source). Depends on wq#2500's live install — file it with that dependency, not as a rival.

**T2 — Agent-cognition metrics and the first rule over them.**
Lane: ops. From `FM2 L923-950`, and the natural pair to FMon1 T2 (`vmalert`).
Emit from fworker/the dispatcher what only exists today as rows and spans: attempt-2 rate,
outcome classification mix, `PERMISSION_LOOP` count, circuit-breaker trips. The transport already
exists — `harness/fworker.py:498` pushes OTLP to VictoriaMetrics — so this is a counter, not a
pipeline. Then evaluate **one** rule: breaker trip-rate or `PERMISSION_LOOP` rate over 1 h → one
aq row, never a page per event, honouring wq#1040 silences and `aq_escalate.py`'s at-most-once
discipline (`aq_escalate.py:13-21`).
Accept: metrics visible in VM for a synthetic run; a replay of the 2026-08-29 shape (8 permission
trips in an hour) produces **exactly one** aq row; a normal day produces none; no new page route
that `alert-deadman-beat.sh` does not cover. Note: FMon1 T2 stands up rule evaluation at all — if
that lands first, this is a rule file; if not, this ticket carries the evaluator and should be
merged with it rather than building a second one.

**T3 — Audit and close the Cloudflare Access header-trust gap.**
Lane: ops (audit first, code second). From `FM2 L1155-1284`, claim verified in §6.
(a) Audit: for each origin behind the tunnel that makes an authorisation decision, determine
whether it verifies `Cf-Access-Jwt-Assertion` or trusts `Cf-Access-Authenticated-User-Email` (or
trusts the tunnel entirely). Path-only reporting; no tokens in the output.
(b) For any that decide anything on identity, add signature verification against
`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`, RS256, `aud` = the application's
audience tag — **with a TTL cache on the JWKS fetch** (the doc fetches per request) and an `iss`
check (the doc omits it).
Accept: the audit table exists with a verdict per origin; one origin verifies end to end; a
request with a forged `Cf-Access-Authenticated-User-Email` and no JWT is rejected 403; a valid
session still passes; JWKS is fetched at most once per TTL under load. Instinct 5 governs any
change to cloudflared itself — this changes origins, not the tunnel.

**T4 — One owner for RAM reclaim (R15, the fleet's own finding).**
Lane: ops. Not from the doc's PriorityClass YAML — from what the doc is reaching for and
`FLEET_SERVICE_ATLAS.md:517` already diagnosed: node-killer, rosco and the self-healer all reclaim
memory with no shared cooldown, and rosco's name-allowlist omits `fworker`/`lumen-run` wrappers,
so a healthy long-running worker can be reaped mid-ticket.
Accept: a shared cooldown file with one declared owner; `fworker`/`lumen-run` wrappers allowlisted
in rosco; a synthetic memory-pressure event produces exactly one kill decision from one actuator;
the reaped-worker case is covered by a test.

**T5 — Reconcile the `3dprinter` node before any fabrication work (R11).**
Lane: fleet, physical dependency. `INVENTORY_RECONCILIATION.md:196`: offline on the tailnet since
2026-07-26 while inventory still claims `power: on`; grade D record. This is a physical task
(power it on or mark it retired), queued as such per instinct 3 — not a gate on anything else.
Accept: the node is either reachable with its hardware facts ingested, or marked retired and
removed from the monitored roster; `power: on` stops being false.

**T6 — Keep the Kured lock correction on file (documentation-only).**
No build. When FMon1 T3 is implemented, the design note is: `weave.works/kured-node-lock` is a
cluster-wide mutex recorded on the DaemonSet, **not** a per-node skip (verified §6). Our analogue
is a per-node block keyed on live `slock` / `wq` claim / `lumen-run` registration, re-checked
after the pre-flight, as FMon1 T3 already specifies. Recorded so nobody re-reads the doc and
copies the wrong primitive.

## 5. What to STOP doing / never start

1. **Stop re-litigating Kubernetes.** Third document in this family to assume it
   (`DIGEST_Fleet_Memory_3_md.md` R8, `DIGEST_Fleet_monitoring_1_md.md` STOP #2, now R2/R12/R14/
   R15/R18 here). `which k3s kubectl helm longhorn velero linkerd argocd` returns nothing. Any
   future recommendation whose first step is "deploy to the cluster" is answered by this line.
2. **Stop planning GPU fine-tuning.** No NVIDIA hardware exists, the AMD cards were retired from
   compute by deliberate decision (wq#2136/2157), and the local-model strategy is CPU MoE model
   *selection* (`LOCAL_MODEL_STRATEGY.md:13-22`). Distillation, LoRA, Unsloth and "route the
   supervisor to a local clone" should drop out of every future fleet plan.
3. **Never build a memory compactor that deletes.** Instinct 7 is explicit and the doc's design
   (`FM2 L741-745`) violates it. Consolidation proposes; Frank disposes.
4. **Stop proposing ingress, portals, OAuth gates and chat UIs.** cloudflared, CF Access + GitHub
   OAuth, Glance and four chat surfaces all exist (R19/R23/R26/R27). Four of this doc's ~28
   recommendations are things we shipped months ago.
5. **Stop proposing backup systems.** The restic chain has hourly/daily/offsite tiers, a prune and
   a monthly integrity verify — strictly more than Velero-as-described. The real backup risk is
   already named and is not tooling: **107 of 118 timers have no dead-man**
   (`FLEET_SERVICE_ATLAS.md:511`), backups included. Fix that, not the backend.
6. **Do not add a third trace store.** Langfuse holds agent traces, VictoriaMetrics holds OTLP
   metrics. An OTel Collector + Jaeger alongside them buys nothing and splits the post-mortem.
7. **Never copy this family's credential shapes.** Passwords in dicts, tunnel tokens in manifests,
   `neo4j/password`, `minioadmin/minioadmin`, and secrets interpolated into `shell=True` command
   lines. Four instances in this file alone (§1); FMon1 had two.
8. **Stop reaching for "an MCP server" as the unit of capability.** Eight of this doc's code
   blocks wrap a single shell command in FastMCP. On this fleet the unit is a `bin/` tool plus a
   systemd timer, which is inspectable, testable and does not need a running server.

## 6. External claims, independently verified

Run this session with `fleet-search` (SearXNG-backed), not from memory:

| Claim | Verdict | Source found |
|---|---|---|
| `weave.works/kured-node-lock` makes Kured skip an annotated **node** (`FM2 L5-7`) | **FALSE as stated** | `kured.dev/docs/configuration/`: the flag is "annotation in which to record locking **node**" — a distributed lock; `kubereboot/charts` `values.yaml` carries `lockAnnotation: ""` with that default. A third-party writeup (marcinkujawski.pl, 2026-03) confirms "Kured uses the DaemonSet resource itself (via annotations) as a distributed lock mechanism". The docs list blocking an individual machine as a *separate* mechanism |
| Cloudflare Access passes a verifiable JWT in `Cf-Access-Jwt-Assertion`, with public keys at `<team>.cloudflareaccess.com/cdn-cgi/access/certs` (`FM2 L1199-1254`) | **TRUE** | Cloudflare One docs, "Validate JWTs" (updated 2026-05-06): "the request will include an application token as a `Cf-Access-Jwt-Assertion` request header"; the JWKS path is confirmed by independent implementations (Caddy community, lua-resty-openidc #185) |
| `plugp100` is the maintained local-KLAP path for Tapo P115 (`FM2 L172, L199-213`) | **TRUE** | `petretiandrea/home-assistant-tapo-p100` — the Home Assistant Tapo integration is "provided by plugp100". Relevant only as confirmation; our plug layer already works |
| Unsloth fits an 8B LoRA on a single consumer card (`FM2 L396-408`) | **TRUE but inapplicable** | Unsloth's own requirements docs (2026-08) plus third-party writeups: "a 7B or 8B model now trains on a single consumer card". We have no such card (R8) |

Live fleet checks this run: `which k3s kubectl helm longhorn velero linkerd argocd` → rc=1, no
output; no `vmalert` in `/home/lumen/bin`; no NVIDIA GPU in `INVENTORY_RECONCILIATION.md`; no
klipper/moonraker/octoprint service in `FLEET_SERVICE_ATLAS.md` (only the offline `3dprinter`
inventory record); Langfuse, Glance, MinIO, restic, Home Assistant, Tailscale and cloudflared all
present.

## 7. The one-line version for Frank

This doc finally delivers the two sections Fleet monitoring 1 broke off mid-sentence (drive health
and node locking) and both were already scoped from that digest; the genuinely new things are a
**discovery watcher that files research tickets from the outside world** — the structural fix for
the fact that you keep finding this prior art and we don't — a **metric and one rule over agent
retry behaviour**, and a **small auth gap** where services behind Cloudflare Access trust the
header instead of verifying the signed token. Everything else is either something we already run
(Tailscale, Langfuse, restic, Glance, plug control, cloudflared, reflexion — which landed the same
day this doc was read) or Kubernetes. On discovery specifically: we are not blind, we are
narrow — `github_scout` ranks by star count so it keeps surfacing tools we already run, which
wq#1717 measured and wq#2500 is already fixing; T1 just adds the sources outside GitHub.

## 8. Harness notes (extends `DIGEST_Fleet_monitoring_1_md.md` §8)

That digest's §8 held exactly: `/home/lumen/scratch/TWO/` is outside the worker's `--add-dir`
set, shell `wc`/`head`/`grep` against it are refused and not retryable, and the **`Read` tool
reaches it** — this 1286-line doc was read in five bounded `Read(offset, limit)` calls.

One new refusal shape, logged as `kb/ISSUE-LOG.md` #663:

- **`cd <abs-dir> && ls | grep …` is refused even when every segment is allow-listed**, with
  "ls reads a file by a relative path after a cd in a compound command; which file that is cannot
  be resolved statically while a `Read()` deny rule is configured, so this needs approval." The
  deny-rule evaluator needs a statically resolvable path, and `cd` makes the argument dynamic.
  Workaround: never `cd &&` — pass the absolute path to the tool (`ls /home/lumen/bin | grep …`).
  This is distinct from #653/#659 (substring matching of denied verbs); here nothing is denied,
  the path is merely unresolvable.

Also re-confirmed, consistent with #659: a `fleet-search` query string can itself be refused while
the same tool runs fine on a reworded query — one search was denied and the rephrased version
returned results. The query text is the problem, not the command; reword, do not retry.
