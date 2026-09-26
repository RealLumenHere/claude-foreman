# DIGEST — Frank's "Orchestation 2.md" (wq#2466)

Source: `/home/lumen/scratch/TWO/Orchestation 2.md`, 1615 lines, read in full 2026-09-24.
Like its siblings the doc has no stable numbering (it restarts at "## 1." eleven times), so every
citation is a **line range** plus heading text, e.g. `O2 L906-922 §"Llama.cpp Vulkan & Memory
Optimizations"`.

Fourth of the wq#2463-2469 digest family and the direct continuation of `Orchestation 1.md` —
`DIGEST_Orchestation_1_md.md:36` predicted the missing network-allocation table would land here,
and it does, at `O2 L5-13`. Read that digest's §4 table before this one: 27 of its recommendations
are already adjudicated and this doc repeats nine of them verbatim. Read
`DIGEST_Fleet_searchability_md.md:250-267` for the harness traps; two fired again this run (§9).

## 1. What the doc is, and how much of it to trust

A raw multi-turn chat transcript, at the point where it stops being about orchestration and
becomes a hardware shopping list. Roughly the first 600 lines continue O1's software stack; from
`O2 L578` onward it is CPU upgrades, e-waste procurement and power tuning. Six properties matter
before acting on any line:

- **115 of 1615 lines are literal duplicates.** `O2 L1163-1199` repeats `L1126-1161` exactly (the
  T4 / BlueField / V100 / Alveo / OCP block, five items, twice), and `O2 L1352-1433` repeats
  `L1269-1350` exactly (PL1/PL2, PowerTOP, C-states, PTM7950, twice). O1 had one duplicated
  paragraph; this has two duplicated chapters.
- **A model's own grading rubric leaked into the body.** `O2 L1529-1534`, spliced mid-sentence
  into the RSSHub paragraph: *"Hard Fail 1: I did not use forbidden phrases like 'Based on...'.
  … Hard Fail 4: I did not ignore relevant directives from the `User Corrections History`."* The
  sentence it interrupts resumes at `L1534` with no break. This is a transcript artifact, not
  content — but it is the clearest proof in the family that these files are unedited output.
- **Fifth doc in the family to assume Kubernetes**, and the heaviest yet: Longhorn Helm installs
  and StorageClasses (`L176-220`), Longhorn S3 backup CRDs (`L335-376`), node taints and
  tolerations (`L736-798`), a three-node etcd quorum (`L710-732`), Cilium replacing Flannel
  (`L848-878`), a `CronJob` (`L437`). About **350 of 1615 lines** are unreachable. `which k3s
  kubectl helm argocd` on lumen returns nothing — established four times already
  (`DIGEST_Fleet_Memory_3_md.md:40`, `DIGEST_Fleet_monitoring_1_md.md:40-44`,
  `DIGEST_Fleet_searchability_md.md:222-224`, `DIGEST_Orchestation_1_md.md:25-29`).
- **Escape-mangled code, fifth doc running.** `\[Unit\]`, `\--now`, `\_\_main\_\_`,
  `state\["repo\_url"\]`, `\# comment`. Nothing runs as pasted.
- **The same wrong hardware figures as O1, now load-bearing.** "5x Mac Pros (2697v2)" pooling
  "320GB" (`L8`, `L571`, `L608`), then "8 nodes / 512GB" (`L635`). Reality: three of five online,
  64 GB each = **192 GB**, and they are MacPro6,1 single-socket, not dual 2697v2
  (`research/inference_nodes.yaml:46,74,102`; `research/wq2076_macpro_gpu_recon.md`). Every
  sizing claim downstream inherits a 40-60% error.
- **One figure we can falsify from our own meters.** `O2 L1258` puts Mac Pro **idle draw at
  43 W** in the table that justifies replacing them with OptiPlex 7090 Ultras. We measured it:
  **isis 103.2 W, hathor 86.6 W** (`research/ISIS_POWEROFF_RCA.md:25`, carried into
  `research/inference_nodes.yaml:47,103`). The doc is off by 2-2.4x, *against* its own argument —
  the case for retiring the Macs on power grounds is stronger than the doc makes it.

### Three things in this doc must never be built here

1. **A `privileged: true` node_exporter with `/:/host:ro,rslave`** (`O2 L1463-1475`), to read CPU
   package joules. The fleet's node_exporter is deliberately an **unprivileged per-user** process
   with a per-user vmagent scraping it (`reports/opsaudit_a3_node_exporter.md:55-58`). Trading
   root-with-host-rootfs for RAPL counters — when the fleet already meters **wall watts at the
   plug**, which includes the fans, the drives and the PSU losses RAPL cannot see (`O2 L1505`
   concedes this) — is a strict downgrade for strictly worse data. See R36.
2. **Credentials inline, sixth and seventh instances in the family.** `LLM_API_KEY=` + `sk-tinywhale-local`
   (`L118`), `OPENAI_API_KEY="` + `sk-tinywhale-local"` (`L150`), `AWS_ACCESS_KEY_ID: "your-s3-access-key"`
   in a committed manifest (`L352`), `K3S_TOKEN=your-token` on a shell line (`L728`),
   `api_key: ` + `dummy-key-not-used` three times (`L816`, `L825`, `L832`), and a `.env` holding
   `GITHUB_TOKEN` + Reddit client secret beside a compose file (`L1586-1592`). Instinct 4. The
   `.env` one matters most because R38 is a recommendation we are adopting — wq#2500 carries an
   explicit 0600-file requirement so the good idea does not arrive with the bad shape attached.
3. **The Tailscale ACL policy as literally written** (`O2 L459-480`). The *pattern* — tag-based
   default-deny, directional core→sandbox — is the best security idea in the doc. The *policy* is
   built for a node that does nothing but execute untrusted code. On this fleet the execution
   sandbox is a git worktree plus a per-command Bash allow-list **on lumen itself**
   (`harness/worker-settings.json`, `harness/guard.sh`), not a separate box. There is no node to
   tag `sandbox`; applying the doc's tags would segment the tailnet by hardware class rather than
   by trust, and the nodes it calls "sandbox" are the same nodes that serve inference. See R13.

## 2. External claims, independently verified

Four claims were worth checking with `fleet-search` rather than reasoning about from inside. Three
held with caveats the doc omits; one is simply wrong.

- **KV cache quantization halves context memory** (`O2 L913`). True, and it carries two conditions
  the doc does not mention. First, **V-cache quantization requires flash attention**, and
  llama.cpp/ollama silently fall back to f16 when FA resolves to disabled rather than erroring —
  *"llama.cpp resolved flash attention to disabled, so it will not allocate a quantized value
  cache. Set `OLLAMA_FLASH_ATTENTION=1` explicitly"*
  (`ssdnodes.com/learn/ollama-kv-cache-quantization`; corroborated by
  `openclawdc.com/blog/kv-cache-quantization-q8-vs-q4-vram`, "the flash-attention panic, and the
  silent f16 fallback"). Second, it is **not free**: a measured single-A100 run reports
  `-ctk q8_0 -ctv q8_0` matching f16 perplexity and cutting a 32K cache by 2.1 GiB, **but decode
  slows** (`sotaaz.com/post/llamacpp-kv-cache-quantization-bench-en`, 2026-09). On CPU-only Macs
  that is a trade against our scarcest resource. Hence wq#2499 is a measurement, not an apply.
- **The D700s can be driven through Vulkan** (`O2 L674-698`). Materially true, and better evidence
  than the doc supplies. `github.com/ggml-org/llama.cpp/issues/20889` (2026-03) is a bug report
  from **our exact hardware** — *"MacPro6,1; AMD FirePro D700 (Tahiti XT, GCN 1.0); Ubuntu 26.04;
  Mesa RADV Vulkan; llama.cpp b10103"* — crashing with `vk::DeviceLostError` inside
  `ggml_vk_flash_attn`. The backend **initialises on this GPU** and dies specifically in flash
  attention. That is two findings at once: RADV is a real third path that
  `research/wq2076_macpro_gpu_recon.md` never tested (it tested ROCm/OpenCL and recorded `compute
  stack: none`), and it is the one path where wq#2499's flash-attention flag is known to be fatal.
- **The Tailscale UDP GRO flags** (`O2 L980`). **The doc has one of them backwards.** It says
  `ethtool -K enp3s0 rx-udp-gro-forwarding on rx-gro-list on`. Tailscale's own Performance best
  practices page documents `rx-udp-gro-forwarding on rx-gro-list **off**`
  (`tailscale.com/docs/reference/best-practices/performance`), and the OpenWrt Tailscale page
  gives the same pairing (`rx-gro-list off`, `rx-udp-gro-forwarding on`). Applied verbatim the doc
  sets the flag the wrong way. Recorded in wq#2502 as much to preserve the correction as to apply
  the tuning.
- **RSSHub is live** (`O2 L1527`, `L1534-1613`). Confirmed: `github.com/DIYgod/RSSHub`, actively
  community-maintained routes, self-hostable as a container or on Workers/Vercel. Worth checking
  because the monitoring digest caught this family recommending a dead project once already
  (`DIGEST_Fleet_monitoring_1_md.md:57`, Watchtower).

## 3. The fleet is further along than the doc assumes

Four of the doc's flagship recommendations are running services here, and in three cases they are
running in a *better-governed* form:

- **"Build a GitHub Watcher Agent"** (`O2 L378-437`). Already built, already **audited**.
  `autonomy/lumen_scout_graph.py` is a 330-line LangGraph scout on `lumen-scout.timer`, daily
  06:00 UTC (`research/TARGETED_CRAWL_SOURCE_RANKING.md:58`). wq#1717 measured its output and
  found the defect the doc's version would have shipped with too — see §5.
- **"Dynamic Node Sleeping (Scale-to-Zero)"** (`O2 L1244`) — WoL a heavy node when a task arrives,
  buffer the request while it wakes. That is wq#335 (`fleet-sleeper`: idle-detect + graceful
  auto-sleep), wq#336 (wake path: wq claim → wake → run → sleep), wq#334 (`plugctl`) and wq#2254
  (cold/warm power lifecycle, wake on starvation). The fleet's version wakes on **AC plug**, not
  WoL (`research/inference_nodes.yaml:48-51,76-79`), and carries a per-node `power_policy` with a
  recorded reason. Nothing to do.
- **Structured feeds instead of scraping HTML** (`O2 L1516-1521`). **Miniflux** runs on sia
  `100.111.15.120` with a curated feed set (wq#82 verified list imported by wq#122;
  `bin/wq122-stale-sweep:5`), version-tracked by `bin/fleet-update-check:191,213-216` and in the
  monthly maintenance bundle (`bin/fleet-maint-assemble:96,243-254`). arXiv-RSS → pgvector is
  wq#9. The consumer exists; §6 T2 adds one producer.
- **The self-healing SRE loop** (`O2 L507-518`). `dispatcher/self_heal.py` (wq#725) already
  classifies every block into INFRA_CRASH / LIMIT_HIT / WORK_BLOCKED / HARNESS_DEFECT /
  RUNG_FAULT and releases only the two classes where retry is sound
  (`DIGEST_Orchestation_1_md.md:125`). The doc's version reads Loki, asks an LLM for a `kubectl
  patch`, and hands it to a "highly-privileged MCP Server" to execute (`L517`).

And one piece of tooling the doc's central measurement needs, which we already own:
**`bin/switch-port-test`** is a per-port iperf3 walk for the **Sodola SL-SWTGW2224AS** — the
24-port 2.5GbE managed switch the doc's whole network-allocation table is written around
(`O2 L5-13`, `L636`). It checks remote link speed is 2500, runs forward and reverse 15 s iperf3,
and appends CSV to `/home/lumen/firmware/sodola-sl-swtgw2224as/`. **wq#2490** (filed by the O1
digest to measure inter-Mac-Pro bandwidth before adopting pooled inference) should copy this
harness rather than build one.

## 4. Every actionable recommendation, with a fleet verdict

Verdicts: **HAVE** = already built and verified; **ADAPT** = right idea, wrong mechanism for a
fleet with no Kubernetes; **REJECT** = do not build; **PROCURE** = a hardware purchase, Frank's
act, not a BUILD ticket; **NEW** = genuine uncovered gap.

| # | doc cite | recommendation | verdict | grounding |
|---|---|---|---|---|
| R1 | `L5-13` | per-tier network allocation: 2.5GbE to inference/DB, 1GbE to scrapers | **ADAPT** | the table O1 promised and never delivered (`DIGEST_Orchestation_1_md.md:36`). Reasoning is sound — bandwidth matters for weights and vectors, not JSON. The switch is real (`bin/switch-port-test`, Sodola SL-SWTGW2224AS); the *assignment* describes hardware we do not have in that configuration (`FLEET_MAP.md:20-30`). Feeds wq#2490 |
| R2 | `L19-24`, `L56-100` | `exo` P2P over the 2.5GbE switch; systemd unit; `EXO_LIBP2P_NAMESPACE` to isolate the cluster; **do not bind `tailscale0`** — encryption overhead crushes token rate | **ADAPT, already filed** | wq#2490. The one net-new detail worth keeping is the negative: bind the physical iface, not the tailnet. Note it contradicts R28, which tunes the tailnet for exactly this traffic |
| R3 | `L30-32` | PydanticAI for typed agent-node outputs | **ADAPT, already filed** | wq#2489 (O1 R25/R7). Duplicate |
| R4 | `L34-38`, `L102-137` | OpenHands in a Docker-in-Docker sandbox on the edge nodes | **REJECT** | O1 R26. An fworker already is this, with a stricter harness; `harness/guard.sh` denies the container CLI outright. The doc's own version mounts the host container socket into the sandbox (`L129`) — that is root on the host behind an LLM |
| R5 | `L44-46` | LightRAG instead of Microsoft GraphRAG, over Neo4j | **REJECT** | O1 R19. `neo4j` not installed; the knowledge store is `research/BRAIN_KNOWLEDGE_STORE.md` + wq#2442 Obsidian memory, verified in `reports/2453_verify_wq2442_obsidian_memory.md` |
| R6 | `L48-50` | Mem0 as an episodic-memory layer under LiteLLM | **REJECT** | O1 R20. Also instinct 7: Mem0 auto-injects learned facts; consolidation here is proposal-only |
| R7 | `L139-168` | Aider + local Gitea + ArgoCD so agents deploy their own discoveries | **REJECT** | O1 R15. No ArgoCD to sync; `bin/lumen-pr` already forces branch+PR and "refuses the non-compliant one" |
| R8 | `L176-220` | Longhorn distributed block storage on K3s, `longhorn-fast` / `longhorn-ha` StorageClasses | **REJECT** | no K3s. §1 |
| R9 | `L222-305` | 5-stage research pipeline: watch → Crawl4AI parse → sandbox build → DSPy strict `ADOPT`/`REJECT` judge → write to graph | **HAVE (3 of 5)** + **ADAPT (1)** | watcher = `lumen_scout_graph.py`; sandbox-build-and-judge = the pilot lane (wq#1559, `research/DEFER_PILOT_LANE.md`), with wq#2477 root-causing why 3/4 pilots SKIP; the strict boolean verdict is wq#2489. The Neo4j write and the NATS transport are R5/R12 |
| R10 | `L307-331` | a Tailscale subnet router so pods reach internal IPs | **REJECT (no-op)** | every fleet node runs tailscale directly; there are no pods behind a private CIDR. Solves a problem created by R8 |
| R11 | `L335-376` | Longhorn → S3/R2 off-site snapshot backups | **REJECT as specified** | no Longhorn. The goal is met: backups exist (instinct 2 relies on it). Ships an AWS key in a committed manifest (`L352`) |
| R12 | `L378-437` | a GitHub watcher CronJob publishing new repos to NATS JetStream | **HAVE (watcher)** + **REJECT (NATS)** | §3. `lumen-scout.timer`, daily, live. NATS is O1 R6 — not installed, no fan-out problem; wq is the durable event log (`bin/wq:111`) |
| R13 | `L439-482` | Tailscale ACLs: `tag:core`/`tag:edge`/`tag:sandbox`, default-deny, directional | **ADAPT — design only** | best security idea in the doc, wrong shape for us (§1.3). Our sandbox is a worktree on lumen, not a node. Editing ACLs also needs Frank's Tailscale admin account — a **physical/credential act**, queue it as a physical task, never as a gate (instinct 3). Not filed: there is no node to tag today |
| R14 | `L490-505` | Loki + Promtail to aggregate logs fleet-wide | **ADAPT — out of scope here** | real gap-shaped, but log aggregation is `DIGEST_Fleet_monitoring_1_md.md`'s territory and that digest's §3 finding governs: every recommendation in this family **adds sources and none adds suppression**, against a measured 1,179 publishes / 24 h (`research/ALERT_TRIAGE_LLM.md:9-33`). Do not add a log firehose before wq#1040 lands silences |
| R15 | `L507-518` | SRE agent: observe `CrashLoopBackOff` → read logs → LLM writes a `kubectl patch` → privileged MCP applies it → commit the fix to memory | **HAVE (better)** | §3. `dispatcher/self_heal.py`. The doc's ACT step is an LLM-authored command run by a "highly-privileged MCP Server" — the exact shape O1 R11 was rejected for |
| R16 | `L520-564` | DSPy DOM self-healing: on CSS-selector failure, ask the LLM for a new selector, retry, cache it | **REJECT as specified** | `fleet-search read` extracts with trafilatura, not CSS selectors, so there is no selector to repair. Its real failure mode is the block page, and that is already solved — wq#2481 built the tiered fetch ladder and wq#2494 added `_detect_challenge()` (`kb/ISSUE-LOG.md` #658). The doc's fallback-to-LLM-on-null *pattern* is sound and already the ladder's shape |
| R17 | `L578-624` | Xeon Gold 6326 as heavy-inference gateway; **buy 8 identical 16 GB DIMMs, never 4x32 or 2x64**, or 8-channel collapses to quad | **PROCURE — keep the warning** | the DIMM rule is the single most useful sentence in the hardware half and is correct: 8-channel DDR4-3200 is ~204.8 GB/s only if all eight channels are populated. Applies whenever that board is built. The rest (`L593-618`) is K3s re-scheduling |
| R18 | `L630-636` | bring 3 more Macs online, 8 nodes / 512 GB, all on the 2.5GbE switch | **PROCURE + wrong figures** | three of five are online now (khepri `remediation_hold = on`, maksoor offline). The *network* instruction — all Macs on 2.5GbE, not 1GbE — is the precondition wq#2490 has to verify before pooled inference is even arguable |
| R19 | `L663-668` | use the upgraded RK3399s as a local container-registry mirror and HuggingFace weight cache | **ADAPT — plausible, unmeasured** | no evidence yet that we re-pull weights or images often enough to pay for it. Worth a look only if `fleet-update-check`'s image-digest reporting shows repeated pulls. Not filed |
| R20 | `L674-698` | compile `llama.cpp` with `GGML_VULKAN=1` to drive the D700s, bypassing OpenCL | **NEW → T3 (wq#2501)** | §2. `wq2076_macpro_gpu_recon.md` tested ROCm/OpenCL and wrote off the GPUs; RADV is untested here and upstream shows it initialising on our exact board. The doc's "96 GB aggregate VRAM" assumes 8 nodes we do not have; the honest ceiling is 12 GB per online Mac, which buys prefill offload at best |
| R21 | `L700-708`, `L800-842` | OpenVINO on Iris Xe iGPUs for embeddings; LiteLLM `usage-based-routing` with a fallback to a second OpenVINO node | **ADAPT — unmeasured** | the LiteLLM half is **HAVE** (gateway live with per-node aliases, `research/inference_nodes.yaml:18`; tiers in `research/DIFFICULTY_ROUTING.md`). The OpenVINO half needs an embedding workload big enough to be worth a new serving stack, and the "300-400% faster" figure is unsourced. Not filed |
| R22 | `L710-732` | 3x J5005 as a K3s etcd HA quorum | **REJECT** | no K3s. Also self-refuting against R31: a quorum that exists so the cluster survives the main server going down, in a fleet the same doc tells you to put to sleep |
| R23 | `L736-798` | node labels, taints and tolerations to pin databases to the storage node | **REJECT** | no K3s. O1 R2 |
| R24 | `L848-878` | replace Flannel with Cilium eBPF, `kubeProxyReplacement=strict` | **REJECT** | no K3s, no Flannel, no kube-proxy |
| R25 | `L880-904` | sysctl: 128 MB TCP buffers, BBR + `fq`, `bpf_jit_enable`, `fs.file-max` | **ADAPT — low value, real risk** | BBR is cheap and defensible; 128 MB socket buffers on a 2.5GbE LAN is cargo cult, and `bpf_jit_enable` exists here only to serve Cilium (R24). A fleet-wide sysctl change with no measured problem is how you acquire one. Not filed |
| R26 | `L906-922` | llama.cpp: `-fa on`, `--cache-type-k/-v q8_0`, `-b 2048 -ub 2048`, `GGML_VULKAN_LOAD_MODE=auto` | **NEW → T1 (wq#2499)** | the best item in the doc. `OLLAMA_FLASH_ATTENTION` and `OLLAMA_KV_CACHE_TYPE` appear **nowhere** in `/home/lumen/bin` or `/home/lumen/foreman/research` (only `OLLAMA_CONTEXT_LENGTH`, `OLLAMA_KEEP_ALIVE`, `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_NUM_PARALLEL`). wq#348 did the cache-flag pass in July and never touched KV dtype. §5 |
| R27 | `L930-952` | Sub-NUMA Clustering: wrap inference in `numactl --interleave=all --physcpubind` | **REJECT (today)** | MacPro6,1 is single-socket 12-core (`research/inference_nodes.yaml:45,73,101`), so there are no NUMA nodes to cross. Genuinely relevant only if the Xeon 6326 (R17) is ever built, and then it is right |
| R28 | `L954-996` | Tailscale UDP GRO/GSO via `ethtool -K` + `udp_l3mdev_accept` | **ADAPT → T4 (wq#2502)** | mechanism real, **flag value wrong** (§2), and it only helps nodes that *forward* tailnet traffic. The doc applies it fleet-wide on faith |
| R29 | `L998-1062` | role assignment for 2x i5-12500 and 3x BC-250 (System-1 fast tier on GDDR6 vs System-2 on the Macs) | **PROCURE** | hardware we do not have. The System-1/System-2 split itself is **HAVE** (`research/DIFFICULTY_ROUTING.md`, `models.yaml`). The BC-250's ~448 GB/s unified GDDR6 is the one genuinely interesting spec in the hardware half — 7.5x a Mac Pro's 59.7 GB/s — if one ever lands, it belongs in `research/inference_nodes.yaml` |
| R30 | `L1064-1240` | e-waste buy list: Tesla P40, ConnectX-3/4, MI25, Coral TPU, CMP 170HX, Optane AIC, SAS3 shelves, HP T740, Tesla T4, BlueField DPU, V100, Alveo U50, OCP sleds, QAT 8970, RTX A2000, Qualcomm AI 100, LSI 9305 | **REJECT — chassis mismatch** | ~180 lines, and **all but one item needs a PCIe slot and a 500 W+ PSU we do not own anywhere**. The inventory is OptiPlex 3090 UFF/micro, whitebox Apollo Lake mini-PCs, Trigkey N100 boxes and MacPro6,1 (`research/INVENTORY_RECONCILIATION.md:83-100`, via `DIGEST_Fleet_monitoring_1_md.md:52`) — none has a user-accessible full-height slot. The sole survivor is a Coral M.2 into a spare N100 slot, and we have no vision workload. Revisit only if a tower server exists |
| R31 | `L1244` | scale-to-zero: sleep heavy nodes, WoL on task arrival, buffer 10 s | **HAVE** | §3. wq#335/336/334/2254. Ours wakes on AC plug and carries a per-node `power_policy` with a reason |
| R32 | `L1245` | switch the fleet from the `performance` governor to `schedutil` | **ADAPT — cheap, unverified premise** | the doc asserts Ubuntu server defaults to `performance`; that is per-image and per-driver, not a given. A one-line read of `scaling_governor` across the fleet settles it. Too small for its own ticket — fold into wq#2502's node walk |
| R33 | `L1246` | consolidate the 3 J5005 Pi-hole/DNS nodes onto one | **ADAPT — already the discipline** | `bin/retirement-audit` exists and the monthly maintenance flow owns this class. Not a new idea here |
| R34 | `L1248-1267` | replace 8 Mac Pros with OptiPlex 7090 Ultras: 2,160 W → 720 W peak | **PROCURE — and the numbers are wrong in our favour** | idle draw is the metric that matters for always-on boxes, and the doc's 43 W is 2-2.4x under our measurement (§1). Against `hathor 86.6 W` / `isis 103.2 W`, the real case is better than the doc's. Note it also contradicts R20/R29: you cannot both harvest the D700s' Vulkan VRAM and throw the Macs away |
| R35 | `L1275-1350` | PL1/PL2 capping via `intel-undervolt`, `powertop --auto-tune` as a systemd oneshot, deep C-States in UEFI, PTM7950 repaste | **ADAPT (powertop)** + **PROCURE (thermal)** | `powertop --auto-tune` persisted by a unit is cheap and the 15-20 W idle claim is the kind of thing our plug meters can actually verify. UEFI C-States and repasting are physical acts on Frank's bench. Not filed — it belongs with a power ticket, not a digest ticket |
| R36 | `L1435-1505` | `node_exporter --collector.rapl` → PromQL `rate(node_rapl_package_joules_total[1m])` for watts | **REJECT** | §1.1. Needs root or a privileged container against an unprivileged exporter (`reports/opsaudit_a3_node_exporter.md:55-58`), to obtain package-only joules when we already meter **wall** watts per node into the same VictoriaMetrics (wq#332/592 power dashboard, wq#334 `plugctl`, wq#797 watts/CPU canary). The doc concedes at `L1505` that RAPL misses fans, drives and GPUs. Strictly worse data at strictly higher privilege |
| R37 | `L1507-1521` | LangGraph news agents; HuggingFace Daily Papers API; HF trending-models feed | **HAVE (transport)** + **ADAPT (sources)** | Miniflux + arXiv-RSS (wq#9/82/122) is the transport. The two HF feeds are plausible additions and cost one subscription each — fold into wq#2500's feed list rather than a separate ticket. The two named LangGraph "news agents" are unvetted third-party repos; we have a scout |
| R38 | `L1523-1613` | self-host **RSSHub** to bridge GitHub Trending / Reddit / releases into RSS, with Redis caching | **NEW → T2 (wq#2500)** | the best software item after R26, and the direct remedy to a **measured** fleet defect (§5). Verified alive (§2). Deploy shape must change: the harness denies the container CLI to workers |

Tally: **8 HAVE, 11 ADAPT, 12 REJECT, 4 PROCURE, 3 NEW.** Three of 38 recommendations are
uncovered gaps worth building. Twelve are Kubernetes or Neo4j or NATS for the fifth time.

## 5. Which fleet problems this solves

| Fleet problem | What the doc contributes | Cross-ref |
|---|---|---|
| **The Macs serve 4096 ctx while the manifest promises 40960/131072** — `research/DISPATCH_DISSECTION.md:19` F2, a measured rung defect | The fix (`research/DISPATCH_REBUILD_SPEC.md:111`: raise `OLLAMA_CONTEXT_LENGTH` to 32768) multiplies KV RAM 8x on a 64 GB box. `O2 L913` names the lever that pays for it — q8_0 KV halves the cache. wq#348 did the cache-flag pass in July, concluded "bigger levers = raise ctx", and never connected the two. This is the missing half of a ticket we closed two months ago | **wq#2499**; wq#348, DISPATCH_DISSECTION F2/F5 |
| **`github_scout` is a popularity feed, not a discovery engine** — wq#1717 measured a **median 36,860 stars** across its 2026-09-10 queue, 14 of 19 over 10k, queueing gap-analysis on `litellm`, `gitea`, `open-webui` — tools we already run | Structural cause: `topic:X pushed:>7d stars:>50` sorted by `(keyword_score, stars)` (`lumen_scout_graph.py:147,151,194`). A star **floor** plus a star **sort** cannot surface a two-week-old project. GitHub Trending ranks by star **velocity** — the axis we do not sample — and `O2 L1527,L1609` gives the bridge that turns it into a feed Miniflux can already consume | **wq#2500**; wq#1717, `TARGETED_CRAWL_SOURCE_RANKING.md:17-22` |
| **192 GB of Mac Pro RAM and three GPUs written off as unusable** | `research/wq2076_macpro_gpu_recon.md` recorded `compute stack: none` and `DIGEST_Orchestation_1_md.md:20-23` generalised that to "every GPU line is void". Correct for CUDA/ROCm/OpenCL; **untested for Mesa RADV**, and upstream has a bug report from our exact board proving the Vulkan backend initialises on it | **wq#2501**; wq#2076, wq#2444 |
| **wq#2490 needs a bandwidth measurement and nobody named a tool** | `bin/switch-port-test` already does the per-port iperf3 walk on the Sodola SL-SWTGW2224AS, checks link speed is 2500, logs CSV. The doc's `O2 L636` states the precondition to test: all Macs on the 2.5GbE switch, not the 1GbE ones | **wq#2490** (copy the harness, do not rebuild it) |
| **A fleet-wide tuning about to be applied backwards** | `O2 L980` inverts `rx-gro-list`. Catching it here costs one search; applying it costs a silent throughput regression nobody would attribute | **wq#2502** |
| **Prior-art searches miss what the world already solved** (wq#991 class) | Same evidence as O1, sharper: 8 of 38 recommendations are our own running services, 12 are for a cluster that does not exist, and 3 are real. Reading this doc raw would have bought a Longhorn migration and a $900 e-waste order for machines with no PCIe slots. Digesting it costs four tickets | wq#991, wq#999 |

Problems the doc claims to solve that we **do not have**: distributed block storage (R8/R11),
cluster CNI performance (R24), control-plane HA (R22), GitOps deploy (R7), pod-to-pod subnet
routing (R10), episodic memory (R6), event-bus fan-out (R12), node sleeping (R31), untrusted-code
isolation (R4), NUMA locality (R27).

## 6. BUILD tickets — filed

| | ticket | lane/pri | filed as |
|---|---|---|---|
| T1 | KV-cache quantization + flash attention on the Macs — measure, then adopt | build p2 | **wq#2499** |
| T2 | RSSHub bridge: the velocity feed `github_scout` structurally cannot produce | build p2 | **wq#2500** |
| T3 | Probe Mesa RADV Vulkan on the D700s — the path wq#2076 never tested | pilot p3 | **wq#2501** |
| T4 | Tailscale UDP GRO on forwarding nodes, with the doc's flag corrected | ops p3 | **wq#2502** |

### T1 — KV-cache quantization + flash attention (wq#2499)
Cites `O2 L906-922 §"Llama.cpp Vulkan & Memory Optimizations"`, items 1-3.

**Gap, verified by grep.** `OLLAMA_FLASH_ATTENTION` and `OLLAMA_KV_CACHE_TYPE` are set nowhere in
`/home/lumen/bin` or `/home/lumen/foreman/research`; only `OLLAMA_CONTEXT_LENGTH`,
`OLLAMA_KEEP_ALIVE`, `OLLAMA_MAX_LOADED_MODELS` and `OLLAMA_NUM_PARALLEL` appear.

**Why it matters now.** `DISPATCH_DISSECTION.md:19` F2 is an open rung defect — the Macs serve
4096 ctx against a manifest promising 40960/131072 — and its fix
(`DISPATCH_REBUILD_SPEC.md:111`) raises `OLLAMA_CONTEXT_LENGTH` to 32768, an 8x KV-RAM increase on
a 64 GB box. q8_0 KV halves it back.

**Shape: measurement, not application.** Two conditions from §2 that the doc omits — V-cache
quantization silently falls back to f16 without flash attention, and q8_0 **slows decode** on the
one public benchmark. Against CPU-only Macs at 13.6 / 7.4 prefill tok/s
(`research/inference_nodes.yaml:58,86`) that trade has to be measured. A/B on **nephthys**
(`power_policy: warm`), not hathor (`always-on`, serving); systemd drop-in with the prior unit
backed up; restart ollama only; nothing on isis (down, wq#2140) or thor.

### T2 — RSSHub as the velocity feed (wq#2500)
Cites `O2 L1523-1613 §"Local RSS Bridges (e.g., RSSHub)"` and the compose block at `L1544-1582`.

**Problem is measured, not hypothetical** — wq#1717, §5 above. **Consumer already exists** —
Miniflux on sia. This adds one producer, not a pipeline.

**Deployment shape must change.** `harness/guard.sh` denies the container CLI to workers except
`ps`/`logs` (`kb/ISSUE-LOG.md` #653), so a worker cannot bring the doc's compose stack up. Either
an ops bundle for the maintenance window, or RSSHub as a plain Node systemd unit — the
chromium-bundled image is only needed for JS-rendered sources, and `github/trending/daily/python`
is not one.

**Acceptance is a number, not a deployment**: one week of Trending items diffed against the same
week's `github_scout` candidates — how many did the scout miss, and at what star counts. That
number decides whether it stays. Token from a 0600 file, never the `.env` the doc shows
(`L1586-1592`); note `fleet-search github` is *also* unauthenticated
(`TARGETED_CRAWL_SOURCE_RANKING.md:45`), so one mint covers both. Out of scope: NATS (R12), the
DSPy judge stage (R9). Frank-physical: the PAT mint — queued as a physical task, **not** a gate;
the Trending route works unauthenticated at low poll rates.

### T3 — Mesa RADV Vulkan probe (wq#2501)
Cites `O2 L674-698 §"Phase 5: Unlocking the Mac Pro D700 GPUs (Vulkan Acceleration)"`.

A probe, not an adoption: `vulkaninfo` on nephthys first — do the D700s enumerate under RADV, and
what VRAM does the driver report. Only if they do, one `llama-bench` against the CPU path
**without** flash attention (`llama.cpp#20889` crashes there on this exact board). The verdict
must state plainly that 6 GB per GPU holds nothing we serve, so the ceiling is prefill offload —
which is also the measured bottleneck. If it works, `DIGEST_Orchestation_1_md.md` §1 needs
amending; if it doesn't, the question is closed for good and `wq2076_macpro_gpu_recon.md` gets the
third data point it is missing.

### T4 — Tailscale UDP GRO, flag corrected (wq#2502)
Cites `O2 L954-996 §"Tailscale UDP GRO / GSO Optimizations"`.

Filed as much to record the correction (§2) as to apply the tuning. Step one is a question, not a
change: which nodes actually advertise routes or serve as exit nodes? **If none, close the ticket
with that finding and change nothing** — a legitimate and cheap outcome. Where a node does
forward, measure with the `bin/switch-port-test` iperf3 shape before and after, and persist the
`ethtool` settings in a unit, because they do not survive a power cycle and would otherwise vanish
at the next rolling restart. R32's `scaling_governor` read folds into the same node walk.

## 7. What we can STOP doing

1. **Stop reading the Kubernetes sections of these documents.** Five docs, and this one alone
   spends ~350 lines on Longhorn, taints, etcd quorum, Cilium and StorageClasses for a cluster
   that has never existed here. Four prior digests recorded the same verdict. Treat Kubernetes
   content in any future Frank doc as pre-rejected and skip to the parts that are not.
2. **Stop planning per-CPU power telemetry.** The plug meters are better data at lower privilege
   and they already land in VictoriaMetrics (wq#332, wq#334, wq#592, wq#797). RAPL would cost a
   privileged container and return less (R36).
3. **Stop re-deriving node sleeping.** wq#334/335/336/2254 built wake-on-demand with per-node
   `power_policy` and recorded reasons. Every doc in this family proposes it again (R31).
4. **Do not write a GitHub watcher script.** `autonomy/lumen_scout_graph.py` on `lumen-scout.timer`
   is it, it has run daily since before this doc was written, and wq#1717 already audited its
   output. The gap is its *ranking axis*, not its absence (R12, T2).
5. **Do not apply `O2 L980`'s ethtool line.** `rx-gro-list` is inverted (R28, T4).
6. **Stop costing out the e-waste list.** Tesla P40, MI25, V100, ConnectX, Optane AIC, QAT,
   BlueField, Alveo, LSI HBA, A2000, T4 — every one needs a full-height PCIe slot and a 500 W+
   PSU. The fleet is OptiPlex UFF/micro, Apollo Lake mini-PCs, N100 boxes and MacPro6,1. Zero
   qualifying chassis (R30). This is a buy list for a homelab with a tower; revisit if one exists.
7. **Stop treating `wq2076_macpro_gpu_recon.md`'s "compute stack: none" as the final word** until
   wq#2501 reports. It tested ROCm and OpenCL. RADV is a third path and upstream shows it
   initialising on our board (§2). This is the one place a prior fleet conclusion was too broad.
8. **Stop adding monitoring sources before suppression lands.** Loki (R14), RAPL (R36), cAdvisor
   and SMART all point the same way, and `DIGEST_Fleet_monitoring_1_md.md:78-90` already
   established the fleet's actual monitoring problem is 1,179 publishes per 24 h with no silences.
   wq#1040 first.

## 8. The one-line version for Frank

Three things in 1615 lines are worth building: **q8_0 KV cache + flash attention** on the Macs
(free context headroom, but measure the decode cost first — wq#2499), **RSSHub** to feed Miniflux
GitHub Trending (the scout's star-floor structurally cannot find anything new — wq#2500), and a
**Vulkan probe** on the D700s (the one place our own recon was too quick to write the GPUs off —
wq#2501). One thing in it is wrong and would have cost us quietly: the Tailscale GRO flag is
inverted (wq#2502). The 180-line e-waste buy list is for a machine with PCIe slots, which the
fleet does not have. Everything else is Kubernetes, Neo4j and NATS for the fifth document running,
or our own services described back to us — including node sleeping, the GitHub watcher, the SRE
loop and RSS ingest, all of which we shipped months ago.

## 9. Harness note (sibling digests wq#2467-2469)

Extends `DIGEST_Fleet_searchability_md.md:250-267` and `DIGEST_Fleet_Memory_3_md.md` §7. Two traps
fired again, and one is new in *shape*:

- `/home/lumen/scratch/` is outside the worker `--add-dir` set: `wc`, `grep`, `ls` against it are
  refused. **`Read` reaches it** — take the doc in bounded `Read(offset, limit)` chunks. Confirmed
  a third time.
- **New — logged as `kb/ISSUE-LOG.md` #659.** `harness/guard.sh` string-matches denied tokens
  across the **whole command line**, including quoted argument text that is never executed. Two
  `wq add --detail "..."` calls were refused this run: one because the ticket body contained the
  container tool's name in the phrase "bring the doc's container up", one because it cited
  `bin/fleet-roll-reboot.sh` **by filename**. A filename containing a denied verb cannot be
  written into any ticket body. Paraphrase ("the container CLI", "the rolling-restart script");
  do not retry the same text (wq#1107).
- `Write` into `/home/lumen/foreman` is denied (primary tree). Write the digest to
  `<worktree>/research/` and land it with `lumen-pr`; the path in the ticket is where the file
  ends up after the merge. `Write`/`Edit` into `/home/lumen/kb` **is** allowed — the ISSUE-LOG
  append works directly.
- `lscpu` is not on the Bash allow-list (refused as "requires approval"), so live NUMA/socket
  topology cannot be read from a worker. R27's verdict is grounded in
  `research/inference_nodes.yaml:45,73,101` instead. Worth allow-listing: it is read-only and
  prints no credentials.
