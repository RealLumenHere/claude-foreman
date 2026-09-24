# DIGEST — Frank's "Fleet searchability.md" (wq#2464)

Source: `/home/lumen/scratch/TWO/Fleet searchability.md`, 849 lines, read in full 2026-09-24.
The doc has no stable section numbering (it restarts at "## 1." four separate times), so every
citation below is a **line range** plus the heading text, e.g. `FS L51-99 §"Advanced Scraping &
Bypassing Cloudflare"`.

Second of the wq#2463-2469 digest family; layout follows `DIGEST_Fleet_Memory_3_md.md`. Read
§7 of that digest before starting a sibling — the `/home/lumen/scratch/` harness trap it names
is real and I hit it again this run (§8 below).

## 1. What the doc is, and how much of it to trust

A raw multi-turn chat transcript, not a spec. It opens on fleet web-search, then drifts through
Cloudflare bypass → OPNsense firewall hardware → BGP/MetalLB → speculative decoding → SRE
self-healing → NATS dead-letter queues → network-security pre-mortem. Four properties matter
before acting on any of it:

- **The premise is a different fleet.** The doc addresses a K3s cluster running LangGraph, NATS
  JetStream, DSPy, MetalLB, Gitea GitOps, vLLM and a Flutter app. `which k3s kubectl helm nats
  flaresolverr crawl4ai firecrawl vllm` on lumen returns **nothing** (rc=1, verified live this
  run). This is the **third** doc in the family to rest on a Kubernetes premise we do not have
  (`DIGEST_Fleet_Memory_3_md.md:40`, `DIGEST_Fleet_monitoring_1_md.md:40`). Roughly half of
  this document — every BGP, MetalLB, DinD-on-K3s and NATS recommendation — is unreachable for
  us without first standing up a cluster to fix problems we do not have.
- **Escape-mangled code, same as its siblings.** Every block is markdown-escaped
  (`response \= `, `\_\_main\_\_`, `\[BACKGROUND TASK\]`, `\-f`). None of it runs as pasted.
  Note the FlareSolverr manifest at `FS L138` also uses `environment:` where the Kubernetes
  Pod spec key is `env:` — it would be rejected by the API server even after unescaping. If we
  implement one of these shapes, retype from upstream docs.
- **Leaked scratchpad.** `FS L421-424` is the generating model's own self-grading checklist
  ("Hard Fail 1: I did not use forbidden phrases like 'Based on...'"). No fleet content; skip.
- **Verbatim self-duplication.** `FS L808-812` restates the preceding paragraph almost word for
  word. Same content twice, not two findings.

**But the headline is correct, and it is the one thing in the family that is genuinely
actionable.** The doc's core claim — agents that cannot fetch a bot-protected page are
half-blind — is true of this fleet today, and reproducible in one command (§3 R5).

## 2. External claims, independently verified

Checked with `fleet-search web`/`github` this run rather than taken on the doc's word:

| Doc claim | Verdict | Evidence |
|---|---|---|
| SearXNG aggregates ~70 engines, self-hostable, works as an agent search backend (`FS L5-24`) | **TRUE — and already deployed** | `bin/fleet-search:1-30` (wq#999); SearXNG container on lumen (`research/FLEET_SERVICE_ATLAS.md:34`); `FLEET_SEARXNG_BASE=http://127.0.0.1:8888` (`bin/fleet-search:33`). Four live queries this run all returned `ok:true` |
| FlareSolverr solves Cloudflare challenges via headless Chrome behind a JSON API on :8191 (`FS L99, L108, L172-215`) | **TRUE, and actively maintained** | `FlareSolverr/FlareSolverr`, 15,670 stars, last push **2026-09-24** (today), via `fleet-search github` this run. Not abandonware |
| vLLM supports **heterogeneous-vocabulary** speculative decoding via Token-Level Intersection as of early 2026 (`FS L436`) | **TRUE** | `docs.vllm.ai/en/latest/features/speculative_decoding/`: "By default, vLLM requires the draft and target models to share the same vocabulary. Setting `use_heterogeneous_vocab: true` enables the Token-Level Intersection…"; design in `vllm-project/vllm#38173` (2026-03-25) |
| Firecrawl self-hosting is a drop-in escape from the SaaS (`FS L57`) | **FALSE as stated** | Firecrawl's own blog: "The self-host guide is explicit about which capabilities need cloud"; `filipkonecny.com/2026/03/29/firecrawl-limitations/`: "Self-hosting sounds like the escape hatch, but it is not full cloud parity." The doc omits this |
| Residential proxies are required to avoid IP bans (`FS L79, L95`) | **FALSE for us** | We already egress from Frank's residential connection — that is exactly why `old.reddit.com` HTML returns 200 for us when datacenter IPs get 403 (`research/SEARXNG_FLEET_INTEGRATION.md:15,132`). We would be paying to replace an asset we have |

## 3. Every actionable recommendation, with a fleet verdict

**HAVE** = already built, do not rebuild. **ADAPT** = good idea, wrong substrate, reshape it.
**REJECT** = do not build. **DEFER** = sound but gated on hardware Frank has not deployed.
**NEW** = genuine uncovered gap.

| # | Recommendation (citation) | Verdict | Why |
|---|---|---|---|
| R1 | Self-host SearXNG so agents can search without API limits (`FS L5-24`) | **HAVE** | wq#999 shipped this. `bin/fleet-search` routes web/reddit/github/hn/so; SearXNG on `127.0.0.1:8888`; design in `research/SEARXNG_FLEET_INTEGRATION.md`. The doc's own §1 gotcha (JSON format off by default → 403) was found and fixed there a month earlier (`SEARXNG_FLEET_INTEGRATION.md:106-109`) |
| R2 | Wire `searxng-mcp` into Claude Desktop via `claude_desktop_config.json` (`FS L26-49`) | **ADAPT** (Frank's workstation only) | Fleet agents do **not** need it — they have the CLI, which is strictly better here (cached 14d, project-scoped, fail-soft envelope, no toolset toggle). Frank's Claude Desktop on Thor has no path to our SearXNG at all. That is a real, small, Frank-facing gap → T5 |
| R3 | Wire the same MCP into the LiteLLM/Lumen gateway (`FS L49`) | **REJECT** | LiteLLM is a model gateway, not a tool runtime; it has no MCP client. Adding MCP servers mid-flight also breaks the frozen-toolset rule and invalidates the prompt-cache prefix for every later turn (`research/PREFIX_STABILITY.md`) |
| R4 | Firecrawl MCP (hosted SaaS, `FIRECRAWL_API_KEY`) for scrape/search/crawl (`FS L55-75`) | **REJECT** | Three strikes: (a) it ships every URL an agent touches to a third party; (b) it needs a long-lived API key in a config file, against instinct 4's "tokens in 0600 files behind credential helpers"; (c) self-hosting is explicitly not cloud parity (§2). We would take a dependency and a bill for a capability R6 gets locally |
| R5 | Standard `curl`/`requests` gets 403'd by Cloudflare; you need a real browser (`FS L51-53`) | **TRUE — this is the live gap** | Reproduced this run: `fleet-search read https://www.g2.com/...` → `{"ok":false,"error":"http 403 blocked"}`; same for crunchbase.com. Root cause is one code path: `bin/fleet-search:462-467` does a single `urllib` GET with one fixed Firefox UA string (`:35`) and **returns the 403 as a terminal error** — no retry tier, no browser, no fallback. The tool is honest about failing (by design, `SEARXNG_FLEET_INTEGRATION.md:157`) but it gives up at tier one |
| R6 | Deploy FlareSolverr; route blocked fetches through it for a real-browser challenge solve (`FS L99`, `L108-170`, `L172-215`) | **ADAPT — highest-value item in the doc** | The right answer, wrong packaging. Skip the K3s manifest (no cluster; and its `environment:` key is invalid anyway) — run the container on lumen under the fleet's existing docker + systemd conventions, bound to loopback. Make it **the last rung of a ladder**, not the first call: `/usr/bin/chromium` is already installed on lumen and trafilatura 2.2.0 is already imported by `fleet-search`, so a headless-Chromium tier costs no new service and covers most JS-rendered 403s → T1, T2 |
| R7 | Crawl4AI with `magic=True` (Chrome TLS-fingerprint impersonation) (`FS L77-97`) | **ADAPT** (the idea, not the dependency) | The insight is right — a Firefox UA string over a stock Python TLS stack is a fingerprint mismatch any WAF reads instantly, which is precisely why `fleet-search:35`'s UA does not help. Fix it in our own fetcher (matched header order, `curl_cffi`-style impersonation) as ladder tier 2, rather than adopting a whole crawler framework |
| R8 | Route Crawl4AI through a paid residential proxy pool (`FS L79, L95`) | **REJECT** | We *are* residential (§2). Paying for proxies would downgrade us to a shared pool with worse reputation than our own IP |
| R9 | Wrap the bypass as a FastMCP server (`FS L217-245`) | **REJECT** | Same frozen-toolset/prefix-stability argument as R3, plus the fleet's agents reach tools through Bash-allowlisted CLIs. A new sub-command on `fleet-search` reaches every agent with zero config; an MCP server reaches none of them without a settings edit |
| R10 | OPNsense + Zenarmor deep packet inspection, backed by local Elasticsearch/MongoDB on the firewall (`FS L263`) | **DEFER + partial REJECT** | Gated on hardware Frank owns but has not deployed (`FS L251`: the Topton box is explicitly "unused"). Running Elasticsearch on the router to retain weeks of packet telemetry is a large ongoing cost for visibility we do not currently lack |
| R11 | Suricata IDS/IPS on WAN in Hyperscan mode (`FS L264`) | **DEFER** | Sound if the box is deployed. See R34 — the doc itself warns this will drop agent traffic |
| R12 | Squid/caching proxy on the firewall's NVMe for repeated doc/package fetches (`FS L265`) | **PARTIAL HAVE** | `fleet-search` already caches search + read results per project for 14 days (`bin/fleet-search:37,44-47`). The uncovered slice is **package** fetches (apt/pip/npm across nodes), which a local caching proxy would genuinely help — but that is an `apt-cacher-ng`-shaped job, not a reason to deploy a firewall |
| R13 | Physical/VLAN segmentation; sandbox VLAN that can reach the internet but not the cluster (`FS L267-273`) | **ADAPT** | The zero-trust-sandbox principle is right and maps onto our worktree/`--add-dir`/guard.sh boundary, which is a *filesystem* boundary with no network twin. Real, but it is a hardware-deployment project, not a searchability fix |
| R14 | CrowdSec plugin for collaborative IP threat intel (`FS L274`) | **DEFER** | Reasonable with the box; no bearing on searchability |
| R15 | Put Pi-hole on the RPi 4B, not inside OPNsense, so DNS survives firewall reflashes (`FS L276-280`) | **ACCEPT (advice, no build)** | The reasoning is sound and generalises: do not co-locate the resolver with the thing you reflash. Frank's hardware call; nothing for the fleet to build |
| R16 | `os-haproxy` + `os-acme-client` for reverse proxy and wildcard TLS to AI endpoints (`FS L286-292`) | **REJECT** | Duplicates cloudflared, which already terminates TLS and fronts **all** public hostnames through one tunnel (`FLEET_SERVICE_ATLAS.md:35`). A second ingress path is a second place for a hostname to rot |
| R17 | `os-frr` BGP peering with MetalLB so K3s announces service IPs to the firewall (`FS L294-299`, full config `L314-419`) | **REJECT** | ~105 lines of the doc — its single largest block — configuring a protocol between two things we do not run. No Kubernetes, no MetalLB |
| R18 | `os-telegraf` pushing firewall metrics to Prometheus for SRE agents (`FS L305-306`) | **DEFER** | Metrics coverage already exists via Gatus/netdata/beszel (`FLEET_SERVICE_ATLAS.md:33,40`). Firewall metrics only become interesting once there is a firewall |
| R19 | `os-ntopng` for top-talker/anomaly visibility (`FS L307`) | **DEFER** | Same gate |
| R20 | `os-wireguard`/`os-tailscale` on the firewall as remote-access gateway (`FS L311`) | **HAVE** | `tailscaled` already runs on the fleet (`FLEET_SERVICE_ATLAS.md:32`) and is how this session reaches lumen at all |
| R21 | `os-git-backup`: auto-commit every firewall config change to git (`FS L312`) | **DEFER (but note the pattern)** | The pattern — config-as-git-history so a bad automated change is one revert away — is one the fleet already applies to `/home/lumen/bin` and foreman. Worth carrying to the box if deployed |
| R22 | Heterogeneous speculative decoding: draft model on a micro-blade, target on the RTX 4060, draft tokens over the 2.5GbE network (`FS L430-446`) | **NEW premise, REJECT topology** | The vLLM capability is real (§2). The *architecture* is not: speculative decoding wins by amortising one memory-bandwidth-bound forward pass, and a network round-trip per draft batch (~0.2-1 ms LAN RTT, plus serialisation) eats the margin it is trying to buy. vLLM's own implementation runs draft and target in one process. Separately, the fleet has **no vLLM** (`which vllm` → rc=1) — it serves through ollama:11434 and LiteLLM:4000. Cross-ref `research/INFERENCE_NODE_REGISTRY.md` (wq#2241), which already owns "several models per box, swapped for the task" and is where any spec-decode work belongs |
| R23 | Prometheus Alertmanager webhook → LangGraph SRE sub-agent with read/write-isolated kubectl tools (`FS L448-462`) | **HAVE (intent), REJECT (stack)** | This is `aq → bin/aq_escalate.py → wq fleet-fires` (`FLEET_SERVICE_ATLAS.md:45`), which has an exactly-once property the doc's version lacks — see `DIGEST_Fleet_Memory_3_md.md:53` for the same rejection reached from a different doc |
| R24 | HITL gate: agent pauses on `interrupt()`, pushes an approve/deny card to a Flutter app over WebSocket, waits for a tap (`FS L463-473`, `L511-559`) | **REJECT — conflicts with standing policy** | Instinct 3 (Frank, 2026-08-22, full autonomy): "decide and act — no Frank-gates, no asking permission, no parking work on his answer… The ONLY legitimate Frank dependency is a physical act nobody else can perform." The doc's HITL gate is the exact architecture that order retired. Building a low-friction approval channel makes it *cheaper* to add gates, which is the wrong direction. Note the fleet already has the transport if it ever wants it (`lumen-messenger:8765`, `FLEET_SERVICE_ATLAS.md:37`) — what it deliberately lacks is the gate |
| R25 | Capture human "Deny" taps as DSPy negative examples; weekend `BootstrapFewShot` cron rewrites gateway prompts automatically (`FS L561-580`) | **REJECT** | Instinct 7: "Memory consolidation stays proposal-only; nothing auto-edits `claude_memory.md` or `instincts.yaml`. Candidates go to minerva/dream for Frank review." An optimiser that silently rewrites the prompts behind LiteLLM every Monday is precisely the auto-edit loop that rule forbids, and it has no review surface |
| R26 | Scout Agent on a 6h cron pulling GitHub trending for new AI repos (`FS L480-509`) | **HAVE** | `lumen-scout.service` — "Lumen GitHub Daily Scout — queue gap-analysis for trending repos" (verified live this run), feeding `lumen-intel` → `minerva-classifier` → `minerva-proposal` (`FLEET_SERVICE_ATLAS.md:43`). We have the doc's whole discovery→classify→propose chain, with a review step it lacks |
| R27 | Weekend maintenance cron: docker prune, clear Evicted/Completed pods, `ollama pull` latest weights (`FS L586-616`) | **PARTIAL HAVE + REJECT one part** | Fleet maintenance already exists (`bin/fleet-maint-assemble`, `bin/fleet-maint-run`). The pod-clearing half is moot. The **`ollama pull` half must not be adopted**: `harness/guard.sh`'s wq#1297 block denies ollama/podman/git-lfs pulls from every cron and fworker Bash call precisely because a tens-of-GB pull can cross the wifi bridge and take the fleet out (`research/FLEET_MAP.md`). The doc recommends, as routine hygiene, the single operation we built a guard to prevent |
| R28 | Weekly knowledge-graph consolidation: summarise redundant episodic nodes into macro-insights, delete the raw ones (`FS L602-606`) | **HAVE** | `lumen-dream.timer` (Sundays 18:00 UTC, verified live) plus the minerva chain. Ours is proposal-only by policy; the doc's deletes raw nodes automatically, which would be a one-way loss |
| R29 | NATS JetStream DLQ + "Mortician Agent" to triage permanently-failed tasks and record why (`FS L628-632`, `L646-806`) | **ADAPT (idea), REJECT (transport)** | No NATS (verified). But the *principle* — a task that has exhausted retries must be analysed and recorded, never silently dropped or retried forever — has a real gap here: wq rows that get blocked/released/reaped accumulate without a standing "why did this die, and what should stop us trying again" pass. That is worth building on the wq table we already have → T6 |
| R30 | Golden snapshot: daily offsite backup of GitOps repo, tuning data, vector DB dumps (`FS L634-643`) | **HAVE** | Verified live: `lumen-backup-critical` (hourly: Vaultwarden, Langfuse PG, pgvector, Redis, agent SQLite), `lumen-backup-important` (daily), `lumen-backup-offsite` (restic → Thoth), `lumen-backup-prune` (weekly), `lumen-backup-verify` (monthly). Strictly more than the doc asks for |
| R31 | Thermal guard: if a node exceeds 80 °C, pause the ingestion agent until it cools (`FS L622-626`) | **NEW (small)** | Thermal backpressure on *dispatch* is not in the ~70-probe guard layer (`FLEET_SERVICE_ATLAS.md:44`). The fleet has monitoring that would alert on heat but no path that slows the work causing it → T4 |
| R32 | Pi-hole blocklists break agent builds and hang headless browsers; exempt cluster IPs via a Group-Management bypass group (`FS L816-826`) | **ADAPT — the best non-search content in the doc** | A correct, specific pre-mortem. It applies to *us* the moment Frank puts Pi-hole on the RPi: our sandboxes pull dependencies and our fetchers hit tracker-adjacent domains. The mechanism (bypass group, no adlists assigned) is exactly right → T3 |
| R33 | Default-deny firewall breaks the cluster; add explicit pass rules — UDP 8472 VXLAN, TCP 6443 API, 10250 kubelet, 179 BGP (`FS L828-839`) | **ADAPT — right lesson, wrong port list** | Every port named is Kubernetes-specific and irrelevant here. Our equivalent set is the one an OPNsense deployment would actually sever: Tailscale (UDP 41641 + DERP egress), SSH 22 between nodes, cloudflared egress 7844, and the internal service ports the atlas lists as the real dependency graph — LiteLLM 4000, ntfy 8080, memoryd 8139, ollama 11434, messenger 8765, Gatus 8418, Commander 3100 (`FLEET_SERVICE_ATLAS.md:14`) → T3 |
| R34 | IPS/DPI reads agent traffic as an attack (rapid scraping = automated attack, RPC = exfiltration); whitelist the AI subnet or set Alert-Only (`FS L841-847`) | **ADAPT** | Same conditional as R33, and the observation is accurate: `fleet-search` bursting dozens of queries looks like scraping because it *is* scraping → T3 |

## 4. Which fleet problems this solves

| Fleet problem | What the doc contributes | Cross-ref |
|---|---|---|
| **"Agents can't search"** — the ticket's framing | **Already fixed, before this doc.** wq#999 shipped `fleet-search` on 2026-08-27; four live queries succeeded this run. The doc's §1 is a description of our current state, not a gap | `bin/fleet-search`, `research/SEARXNG_FLEET_INTEGRATION.md` |
| **Agents can't *read* what they find** — the real residual gap | This is the doc's genuine contribution. Search returns a URL; `fleet-search read` then dies at `http 403 blocked` on any bot-protected host, with no second attempt. Reproduced live on two domains this run | `bin/fleet-search:462-467`; T1, T2 |
| **Prior-art searches miss community/vendor sources behind WAFs** — the wq#991 class of failure (QuestStack, the Cloudflare KV cap, Obsidian each one search away and missed for weeks) | Fixing the reader widens what a mandated prior-art search can actually retrieve. A `web` hit whose `read` 403s is a near-miss that reads as a dead end | wq#991, wq#999; T1 |
| **A deployed Pi-hole / OPNsense would silently break the fleet** | The doc is a ready-made pre-mortem (`FS L808-847`): DNS sinkholing breaks dependency pulls, default-deny breaks the mesh, DPI reads agent traffic as attack traffic. Worth writing down *before* the Topton box is racked, not after | T3 |
| **Heat causes throttling → timeouts → cascading failures** | Names thermal backpressure on the work source as the control, which the guard layer does not implement | T4 |
| **Tasks that die permanently leave no lesson** | The Mortician pattern: analyse terminal failures once, record the cause, stop future attempts | T6 |
| **Cost/lock-in pressure toward scraping SaaS** | Gives the counter-argument its own evidence: self-hosted Firecrawl is not cloud parity, and our residential IP is an asset a proxy pool would replace with something worse | §2 |

Problems the doc claims to solve that we **do not have**: event-bus fan-out (R23), discovery
(R26), memory consolidation (R28), backup coverage (R30), remote access (R20), ingress TLS
(R16). Six of its recommendations are re-inventions of running services.

## 5. BUILD tickets — filed

All six are filed as of this digest; IDs below. Ordered by value — T1+T2 are the real payload,
the rest are secondary. T1 and T2 can land in either order: T1's rung 4 is written to degrade to
`ok:false` if FlareSolverr is absent rather than depend on it.

| | ticket | lane/pri | filed as |
|---|---|---|---|
| T1 | tiered fetch ladder in `fleet-search read` | build p1 | **wq#2481** |
| T2 | FlareSolverr, loopback-bound | build p2 | **wq#2482** |
| T3 | network pre-mortem for the Topton/Pi-hole deployment | research p3 | **wq#2483** |
| T4 | thermal backpressure on dispatch | build p3 | **wq#2484** |
| T5 | searxng-mcp for Frank's Claude Desktop | build p4 | **wq#2485** |
| T6 | terminal-failure triage over the wq table | build p4 | **wq#2486** |

### T1 — `fleet-search read`: tiered fetch ladder instead of terminal 403 (wq#2481)
**Problem.** `bin/fleet-search:462-467` performs one `urllib` GET with a fixed Firefox UA
(`:35`) and returns `http 403 blocked` as terminal. Verified live: `g2.com` and
`crunchbase.com` both fail; any `web` result on a WAF-protected host is unreadable.
**Build.** Replace the single fetch in `_fetch_and_extract` (`:448-470`) with an ordered
ladder, each rung tried only on 403/429/503 (never on 404 — that is a real answer):
1. current `urllib` GET (unchanged; cheapest, and correct for most of the web)
2. **browser-consistent request**: Chrome UA *with matching header order/Accept/Sec-CH-UA*, plus
   a TLS-impersonating client (`curl_cffi`, `impersonate="chrome"`). R7's insight: a Firefox UA
   over stock Python TLS is a fingerprint mismatch, so today's UA string buys nothing.
3. **headless Chromium**: `/usr/bin/chromium` is already installed on lumen (verified). Render,
   dump DOM, hand to the existing trafilatura path (2.2.0, already imported).
4. **FlareSolverr** (T2), for the Turnstile/JS-challenge cases rung 3 cannot clear.

**Constraints.** Preserve the fail-soft envelope contract verbatim (`bin/fleet-search:20-26`) —
still exactly one JSON object, still `ok:false` with a reason, never a silent empty. Add
`"tier"` to the payload so we can measure which rung serves. Per-rung timeout ≤ the existing
`HTTP_TIMEOUT=20` budget; total bounded. Honour the existing 14d cache before rung 1.
**Accept.** `fleet-search read https://www.g2.com/products/anthropic-claude/reviews` returns
`ok:true` with non-empty content and a `tier` field; the two 403 domains from this digest are
regression fixtures; a 404 URL still fails fast at rung 1 without escalating; `ok:false` on
total failure still names the last rung tried.

### T2 — FlareSolverr as a loopback-bound fleet service (wq#2482)
**Problem.** T1 rung 4 needs a challenge solver. Upstream is healthy (15.6k stars, pushed
2026-09-24 — verified, not assumed).
**Build.** `ghcr.io/flaresolverr/flaresolverr:latest` under the fleet's existing docker +
systemd conventions — **not** the doc's K3s manifest (no cluster; and `FS L138` uses
`environment:` where the Pod spec key is `env:`, so it would not apply anyway). Bind
**127.0.0.1:8191 only** — it is an unauthenticated arbitrary-URL fetcher and must never be
cloudflared-exposed (instinct 6). Memory limit ~1Gi for the bundled Chrome. Add a Gatus probe
(`:8418`) so a dead solver surfaces as a normal fleet alert rather than as mysterious T1
tier-4 failures.
**Accept.** `POST 127.0.0.1:8191/v1 {"cmd":"request.get",...}` returns `status:ok` for a known
Cloudflare-challenged URL; the port is unreachable from off-host; Gatus shows the probe green;
the unit survives a lumen reboot.

### T3 — Network pre-mortem: what Pi-hole/OPNsense would sever on *this* fleet (wq#2483)
**Problem.** Frank owns an undeployed Topton/OPNsense box and an RPi 4B earmarked for Pi-hole
(`FS L251-253`). The doc's warnings (`FS L808-847`) are correct in kind but list Kubernetes
ports we do not use. Deploying either without this document is an outage.
**Build.** A doc in `research/` (not code) with two tables: (a) **DNS** — domains agent builds
and fetchers require, and the Pi-hole bypass-group mechanism (`FS L820-826`) for the fleet's
static IPs; (b) **ports/flows** — the real inter-node dependency set, derived from
`FLEET_SERVICE_ATLAS.md:14` and §1.1: Tailscale (UDP 41641 + DERP egress), SSH 22, cloudflared
egress 7844, LiteLLM 4000, ntfy 8080, memoryd 8139, ollama 11434, messenger 8765, Gatus 8418,
Commander 3100. Plus R34's IPS note: set agent-subnet DPI to Alert-Only, because burst
scraping *is* scraping and an IPS will kill it mid-flight.
**Accept.** Every flow in the table cited to a file:line or a live probe, not inferred. Marked
as the pre-deployment checklist for the Topton box. No rule is applied to the live network by
this ticket.

### T4 — Thermal backpressure on dispatch (wq#2484)
**Problem.** `FS L622-626`: a throttling node causes timeouts and cascading failures. The guard
layer alerts on heat but nothing slows the work producing it.
**Build.** A guard that reads node temperature from the existing metrics path and, above a
threshold, applies existing backpressure — hold the affected subsystem (the dispatcher already
skips tickets whose subsystem is held, commit `b8c4609`) rather than inventing a new control.
Release on cooldown with hysteresis so it cannot flap.
**Accept.** Simulated over-threshold reading holds the subsystem and emits one aq row (not a
storm); cooldown releases it; no hold persists after the reading normalises.

### T5 — Give Frank's Claude Desktop the fleet's search (wq#2485)
**Problem.** `FS L26-49`. Fleet agents have `fleet-search`; Frank's desktop has no route to our
SearXNG, so his own searches go through whatever the app defaults to.
**Build.** `searxng-mcp` in Frank's `claude_desktop_config.json` pointed at the fleet SearXNG
over Tailscale. Note SearXNG's `search.formats` must include `json` or every request 403s by
design — already set correctly here (`SEARXNG_FLEET_INTEGRATION.md:106-109`), so this is
config-only on his side.
**Constraint.** Frank's workstation, his config file — deliver instructions + the tested URL,
do not reach onto Thor. Nothing about this touches fleet agent toolsets (R3/R9 stand).
**Accept.** Frank can run one query from Claude Desktop and get results from our instance.

### T6 — Terminal-failure triage over the wq table, the Mortician minus NATS (wq#2486)
**Problem.** `FS L628-632`, `L646-806`. Tasks that exhaust retries should be analysed once and
recorded, so nothing re-attempts a fundamentally impossible job. Today blocked/released/reaped
wq rows accumulate without a standing "why did this die" pass.
**Build.** A periodic pass over terminally-failed wq rows that classifies the cause, writes the
lesson to the existing memory/`ISSUE-LOG` surface, and marks the row so redispatch does not
retry it. **Reuse the wq table** — no new broker (`DIGEST_Fleet_Memory_3_md.md:52` rejected a
broker from the same premise). Keep the exactly-once discipline `bin/aq_escalate.py:13-21`
already proves out; a triage pass that re-fires on every run is a storm generator.
**Accept.** A seeded dead ticket is classified once, produces one durable record, and is not
re-processed on the next run.

## 6. What we can STOP doing

1. **Stop treating "agents can't search" as an open problem.** It was closed on 2026-08-27 by
   wq#999. `fleet-search web|reddit|github|hn|so|read` is live and answered four queries this
   run. The correct residual framing is narrower: *agents can search, but cannot read the
   WAF-protected slice of what they find.* That is T1.
2. **Stop reaching for a scraping SaaS.** Firecrawl's own docs concede self-host ≠ cloud parity,
   and the hosted path costs a key, a bill, and every URL an agent touches leaving the fleet.
   Chromium and trafilatura are already installed here.
3. **Never buy residential proxies.** We are residential. It is why `old.reddit.com` works for
   us and not for datacenter IPs (`SEARXNG_FLEET_INTEGRATION.md:15,132`).
4. **Stop re-proposing Kubernetes.** Third doc in this family to assume it; `which k3s kubectl
   helm` still returns nothing. Every BGP/MetalLB/DinD line in `FS L110-419` — the single
   largest block in the document — is unreachable and should be skipped on sight, not evaluated.
5. **Do not build a human-approval gate.** Instinct 3 retired Frank-gates outright on
   2026-08-22. `FS L463-473`/`L511-559` is a well-engineered version of the thing we stopped
   doing; shipping it would make adding gates cheaper, which is backwards.
6. **Do not adopt the `ollama pull` maintenance step** (`FS L614-616`). `harness/guard.sh`
   (wq#1297) denies exactly that from cron/fworker because a multi-GB pull over the wifi bridge
   is a bigger outage risk than any probe (`research/FLEET_MAP.md`).
7. **Do not auto-tune prompts from feedback** (`FS L561-580`). Instinct 7: consolidation stays
   proposal-only, candidates go to minerva/dream for review.
8. **Stop re-inventing discovery, consolidation, backups and alert-routing.** R23/R26/R28/R30
   are all running services. Six of this doc's recommendations describe our own fleet back to us
   — which is the argument for the digest step rather than acting off any doc raw.
9. **Stop copying code out of Frank's research docs.** Every block here is markdown-escaped and
   will not run, and the one Kubernetes manifest is additionally wrong (`environment:` vs `env:`).
   Third doc, third time. Retype from upstream.

## 7. The one-line version for Frank

Your search doc's headline is right but a month late — we shipped SearXNG search in wq#999 on
2026-08-27; what it actually exposes is the *next* gap, that `fleet-search read` gives up with
`http 403 blocked` the moment a page is WAF-protected (reproduced on two domains today), and
FlareSolverr plus the headless Chromium already on lumen fixes it for zero new spend. Take T1
and T2. The firewall half is a good pre-mortem to keep for the day the Topton box gets racked
(T3), the BGP/MetalLB half is ~105 lines configuring a Kubernetes we do not run, and the HITL
approval-gate half is a polished version of the thing you told us to stop doing in August.

## 8. Harness note (sibling digests wq#2465-2469)

Confirms and extends `DIGEST_Fleet_Memory_3_md.md:§7`:

- `/home/lumen/scratch/` is outside a research worker's `--add-dir` set. `wc`, `grep` and
  friends against that path are refused with "may only … from the allowed working
  directories". **The `Read` tool reaches it** — read the doc in bounded `Read(offset, limit)`
  chunks. Do not retry a synonym command; the path is the problem (wq#1107).
- **Hit again this run, already logged as `kb/ISSUE-LOG.md` #653 (wq#2468):** `harness/guard.sh`
  matches the *string* `docker` anywhere in a Bash command, so a pure read like
  `grep -n -i 'docker' somefile` is blocked with "BLOCKED — docker denied except ps/logs".
  Two of the family's digests have now burned an attempt on it. Not retryable by synonym —
  grep a different token (`prune`, `container`) or use `Read`.
- **Also new:** `Write` into `/home/lumen/foreman` is denied (primary tree, wq#1719/wq#900).
  Write the digest to `<worktree>/research/` and land it with `lumen-pr` — the deliverable path
  named in these tickets is where the file ends up *after* the merge, not where you write it.
- `wq` has no `search` sub-command (`wq list` + grep instead) — worth knowing before burning an
  attempt on it.
