# NETWORK PRE-MORTEM — what a Pi-hole / OPNsense deployment would sever on this fleet

_wq#2483 · from `research/DIGEST_Fleet_searchability_md.md` T3 (L163-176), R32-R34 (L91-93), which adapt Frank's "Fleet searchability.md" `FS L808-847`. Written 2026-09-28._

**Status: PRE-DEPLOYMENT CHECKLIST for the Topton MW-JSL2.5G-4LV10 (OPNsense) and the RPi 4B (Pi-hole) (`FS L251-253`).** It is not a deployment. This ticket applied no rule, resolver change or DNS setting to the live network. Work through §4 before either box carries traffic.

**Evidence pins.** `foreman@aff449a6` for repo paths, `bin@52f576b` for `/home/lumen/bin/*` (the cited files were clean at that rev). "Live probe" means `ss -ltnuH` on Lumen at 2026-09-28 ~18:25Z, read-only.

## 0. Headline

1. **The mesh is where a firewall does the damage.** None of the service ports in the ticket crosses the LAN in cleartext between nodes. They are either loopback-only, used only on Lumen, or reached over Tailscale IPs (`100.x`), which puts them inside WireGuard. On the wire, a firewall between two nodes sees one UDP flow per pair and nothing else. Block that flow (UDP 41641, or DERP/STUN egress) and every row of §2 fails together, even with a pass rule for every TCP port. The doc's Kubernetes list (`FS L828-839`: 8472/6443/10250/179) maps to **Tailscale + cloudflared + DNS**, not to a list of service ports.
2. **OPNsense's default outbound NAT turns Tailscale into "hard NAT".** pf randomises source ports, so direct peer paths to off-LAN nodes collapse onto DERP relays. The traffic still flows, only slower, which makes it easy to miss. The documented fix is Hybrid outbound NAT with Static Port for the fleet hosts, plus NAT-PMP (§2.1 T3).
3. **Several flows do run over the bare LAN.** They break only if the Topton ends up routing *between* fleet segments (VLANs), because devices on one flat switch never pass through it: Wadjet↔Ma'at vault Postgres replication, Home Assistant, the Tapo plug fleet and WoL broadcast (§2.2).
4. **Per-client Pi-hole bypass only works if Pi-hole sees the real client address.** If OPNsense Unbound (or anything else) forwards queries to Pi-hole, every query appears to come from the firewall. The bypass group then matches nothing and the fleet gets full blocking (§1.2 P4).

## 1. DNS — what Pi-hole must not sinkhole for the fleet

### 1.1 Domains agent builds and fetchers require

The table covers what the fleet is known to call. It is **not** a complete allowlist, because `fleet-search read` fetches arbitrary URLs and SearXNG fans out to upstream engines. That is why the fix is the bypass group in §1.2 and not per-domain whitelisting. Treat this table as the acceptance probe: after deployment, each row must still resolve from a fleet IP.

| # | domain(s) | used by | evidence | if sinkholed |
|---|---|---|---|---|
| D1 | `google.*`, `bing.com`, `duckduckgo.com` (SearXNG upstreams) | `fleet-search web/reddit` → local SearXNG :8888 | `bin/fleet-search:10-11,30` | SearXNG returns empty results. fleet-search is fail-soft (`:21`), so the agent sees "no prior art" and wings it (the wq#991 failure class) |
| D2 | `reddit.com`, `old.reddit.com`, `www.reddit.com` | `fleet-search reddit/read` | `bin/fleet-search:11-12`, `harness/research_phase.py:12,114` | hardware-RE research loses its main source |
| D3 | `api.search.brave.com` | fleet-search Brave engine | `bin/fleet-search:256` | engine drops out |
| D4 | `api.github.com`, `github.com`, `raw.githubusercontent.com`, `objects.githubusercontent.com`, `codeload.github.com` | fleet-search `github`, `gh`, `lumen-pr`, git fetch/push | `bin/fleet-search:382,434-442,492` | **every PR lands fails**; `gh pr` hangs until timeout |
| D5 | `hn.algolia.com`, `news.ycombinator.com`, `api.stackexchange.com` | fleet-search engines | `bin/fleet-search:508,516,532` | engines drop out |
| D6 | arbitrary article/tracker hosts; FlareSolverr's headless Chromium | `fleet-search read` (trafilatura), FlareSolverr :8191 | `bin/fleet-search:12,44` | **the headless-browser hang case (`FS L816-826`, R32).** A page waits on a blocked tracker (NXDOMAIN or 0.0.0.0), headless Chromium blocks until timeout, and the fetch is reported dead |
| D7 | `api.cerebras.ai`, `openrouter.ai`, `api.z.ai`, `generativelanguage.googleapis.com` (gemini/), `api.deepseek.com` (deepseek/), Mistral + OpenAI APIs (paid fallbacks) | LiteLLM :4000 upstream providers | `harness/liveness.py:189,201`; `ops/wq1711-difficulty-router/litellm_config_snippet.yaml:44`; `ops/wq2627-gemini-litellm/litellm_config_snippet.yaml:32`; `ops/wq2434-drift-plan/test_drift_plan.py:23,30`; `ops/wq1406-paid-fallback-guard/audit_fallback_chains.py:26`. (The `openai/` prefix in `ops/inference-registry/litellm_local_block.yaml` points at *local* OpenAI-compatible servers, see S2, not at api.openai.com) | every cloud model rung fails, and the ladder falls to local ollama only |
| D8 | `api.anthropic.com` (Claude CLI's own telemetry hosts too) | every Claude CLI session, including fworkers | `harness/systemd/lumen-despag.service:45` (a cgroup that "lost api.anthropic.com" hung every `claude` child's turn); `ops/wq2428-timer-spend-guard/tests/test_timer_spend_audit.py:87` | the API host blocked means no fworker runs at all, and the documented symptom is a hang, not an error. CLI telemetry hosts are the "blocked telemetry domain" class (`FS L808-826`): expected to fail soft, but *verify, don't assume* |
| D9 | `api.tailscale.com`, `controlplane.tailscale.com`, `login.tailscale.com`, `derp*.tailscale.com`, `log.tailscale.io` | tailscaled, liveness ledger | `harness/liveness.py:132`, `harness/liveness_ledger.yaml:35`; tailscale.com/docs/reference/faq/firewall-ports | `log.tailscale.io` is telemetry-shaped, so expect a blocklist to hit it. Blocking the control plane stops key rotation and new peers, and nodes drift off the tailnet over days, not minutes |
| D10 | `argotunnel.com` (SRV `_v2-origintunneld._tcp.argotunnel.com` → region1/region2), `api.cloudflare.com`, `update.argotunnel.com` | cloudflared (the one tunnel: every `*.lumennode.uk`, **and the Commander pipe**) | `FLEET_SERVICE_ATLAS.md:35,84`; `harness/liveness.py:99`; developers.cloudflare.com …/tunnel-with-firewall/ | every public hostname goes dark, **Commander included**, so remote repair has to go over SSH |
| D11 | `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org` (+ wrangler/npm telemetry) | venv builds, `npx wrangler`, `npx --yes promptfoo` | `LUMEN_PUBLIC_RUNBOOK.md:48-98`, `harness/evals/worker-quality/README.md:74`, `harness/tests/test_guard.py:592` | **the build-false-reject case (`FS L808-826`).** A dependency pull fails and the agent blames the tool |
| D12 | `huggingface.co`, `hf.co`, HF CDN/LFS hosts; `registry.ollama.ai` | local model pulls (manual, off-peak) | `ops/wq1986-quantize-local-models/apply.sh:19-34` (the hf.co→huggingface.co realm redirect) | model pulls fail with a misleading "realm host" error |
| D13 | container registries (`registry-1.docker.io`, `auth.docker.io`, `ghcr.io`) | 35 containers' image pulls on recreate | `FLEET_SERVICE_ATLAS.md:8,33-34` | a recreated container cannot come back up (litellm, ntfy, gatus) |
| D14 | `lumennode.uk` incl. `deadman.lumennode.uk` | deadman beat (off-fleet proof of life) | `FLEET_SERVICE_ATLAS.md:46` | no beat means the off-fleet deadman pages Frank for an outage that isn't one |

### 1.2 The bypass-group mechanism (`FS L820-826`), with the four gotchas that defeat it

Mechanism, as the doc gives it (Pi-hole Group Management):

1. **Groups** → create `Fleet-Bypass`.
2. **Clients** → add every fleet static IP and assign it to `Fleet-Bypass`. **Remove it from `Default`.** Pi-hole puts every client in `Default`, and `Default` carries the adlists.
3. **Adlists** → assign `Fleet-Bypass` **no** adlists (leave it unticked on every list).
4. Leave Default's blocking alone for the rest of the house.

Prior art (via `fleet-search`, 2026-09-28) confirms this is the standard pattern: reddit.com/r/pihole/comments/uvzvs5 "use group management to only block ads for certain IPs"; discourse.pi-hole.net/t/42532. It also shows the ways it silently does nothing:

| # | gotcha | why it bites *this* fleet | check |
|---|---|---|---|
| P1 | the client was never taken out of `Default` | Group membership is additive. `Fleet-Bypass` + `Default` still gets Default's adlists | Clients page: fleet rows show `Fleet-Bypass` **only** |
| P2 | a regex/exact **deny** domain entry is assigned to all groups | domain-list entries have their own group assignment, separate from adlists | Domains page: no deny entry assigned to `Fleet-Bypass` |
| P3 | the client is identified by the wrong address | Fleet nodes talk over Tailscale. If Pi-hole is later offered as tailnet DNS, queries arrive from `100.64.0.0/10` / `fd7a:115c:a1e0::/48`, not from the LAN static IPs. Add both CIDRs as clients in `Fleet-Bypass` (Pi-hole accepts subnets as clients) | query log for a fleet node shows the address you listed |
| P4 | **a forwarder sits in front of Pi-hole** | Pi-hole discourse #42532: with the router proxying DNS, every query came from the router's IP and the no-blocking group "didn't work at all". If OPNsense Unbound forwards to Pi-hole, the same thing happens | fleet nodes point **directly** at Pi-hole, or keep their current resolver (Lumen: systemd-resolved stub 127.0.0.53, live probe) and never use Pi-hole at all |

The simplest safe option is P4's second branch. The fleet does not need ad blocking, so leaving fleet resolvers alone removes this whole table from the risk. Use the bypass group only if Pi-hole is pushed by DHCP to the whole LAN.

## 2. Ports / flows — the real inter-node dependency set

Derived from `FLEET_SERVICE_ATLAS.md:14` (the port graph systemd does not see), §1.1 (`:29-47`) and §1.3 (`:74-90`). Bind addresses are from the live probe. "On the wire" means what a firewall between two fleet nodes, or between the fleet and WAN, actually sees.

### 2.1 Transport (a block here severs everything in 2.2 at once)

| # | flow | proto/port | direction | evidence | OPNsense action |
|---|---|---|---|---|---|
| T1 | Tailscale direct WireGuard | UDP 41641 (source port; peers reply to it) | node↔node, node↔WAN | live probe: `udp 0.0.0.0:41641`, `[::]:41641`; tailscale.com FAQ "let your internal devices start UDP from :41641 to \*:\*" | pass UDP from fleet hosts, src 41641 → any |
| T2 | Tailscale control plane + DERP relays | TCP 443 (HTTPS), UDP 3478 (STUN) | egress | tailscale.com firewall-ports FAQ: "connections to the coordination server … and DERP relays use HTTPS on port 443"; blocking one DERP gives "random pairs" of lost contact | pass egress 443 + UDP 3478. **Do not SNI/IP-pin DERP**: "the set of DERP relays grows over time" |
| T3 | Tailscale NAT traversal | — | egress NAT | tailscale.com/docs/install/opnsense: "Select Hybrid Outbound NAT rule generation … Check Static Port … Allow NAT-PMP" | Hybrid outbound NAT, **Static Port** rule for fleet hosts; enable NAT-PMP. Without this, off-LAN peers fall back to DERP (§0.2) |
| T4 | cloudflared tunnel | TCP+UDP 7844 (http2 / quic); SRV lookup on `argotunnel.com`; 443 optional (API, updates) | egress | `FLEET_SERVICE_ATLAS.md:35,84`; developers.cloudflare.com …/tunnel-with-firewall/ "7844 is allowed for both TCP and UDP" | pass egress TCP+UDP 7844. If only TCP is allowed, cloudflared falls back from quic to http2, which is fine, but IPS must not drop the QUIC attempts halfway (§3) |
| T5 | SSH between nodes | TCP 22 | node↔node (mostly via Tailscale IPs, so inside T1) | live probe: `tcp 0.0.0.0:22`, `[::]:22`; `lumen-powerdash-vmtunnel` → Bes, `thoth-origin` autossh → Thoth (`FLEET_SERVICE_ATLAS.md:36,88-89`) | pass on the fleet segment. Across the WAN, SSH rides Tailscale, so no WAN rule |
| T6 | DNS | UDP/TCP 53 | node → resolver | live probe: systemd-resolved stub `127.0.0.53:53`, `127.0.0.54:53` | if OPNsense intercepts/redirects port 53 (a common "force Pi-hole" NAT rule), exclude fleet hosts or P4 applies |

### 2.2 Service ports (the ticket's list), with where each one actually lives on the wire

| # | service | bind (live probe) | consumers | evidence | on the wire | firewall exposure |
|---|---|---|---|---|---|---|
| S1 | LiteLLM :4000 | `127.0.0.1:4000`, `100.117.66.102:4000`, `[fd7a:115c:a1e0::639:4266]:4000` | on-host lanes + tailnet clients | `FLEET_SERVICE_ATLAS.md:76` | loopback or inside WireGuard | **none directly**, but T1/T2 must pass |
| S2 | LiteLLM → remote ollama :11434 / llama :8095 | remote nodes' ollama | LiteLLM local rungs | `ops/inference-registry/litellm_local_block.yaml:5,15,26,36,48` (all `http://100.x…`) ; `research/inference_nodes.yaml:45,79,107` (hathor/nephthys/isis Tailscale IPs) | inside WireGuard | none directly (T1) |
| S3 | ollama :11434 (Lumen) | `*:11434` | memoryd embeds, embed-guard, research-index, tag-sections | `FLEET_SERVICE_ATLAS.md:81` | on-host | none. Wildcard bind means LAN-reachable, so **deny from LAN** is safe and does not break the fleet |
| S4 | memoryd :8139 | `127.0.0.1:8139` | lumen_agent_v4 lanes, healer | `FLEET_SERVICE_ATLAS.md:82` | loopback | none |
| S5 | ntfy :8080 | `0.0.0.0:8080`, `[::]:8080` | ≈every guard via ntfy-send, alert-queue, deadman beat | `FLEET_SERVICE_ATLAS.md:17,77` | on-host; off-host readers come in via cloudflared (T4) | none directly. Its failure mode is T4/D14: no deadman beat |
| S6 | messenger :8765 | `0.0.0.0:8765` | healer, status-monitor, convo-learn; public via cloudflared | `FLEET_SERVICE_ATLAS.md:83` | on-host + T4 | none directly |
| S7 | Gatus :8418 | `*:8418` | model-watchdog, sentinel-probes, sentinel-watch, gatus-ntfy-relay | `FLEET_SERVICE_ATLAS.md:86` | on-host | none directly. Gatus's *outbound* probes to other nodes ride T1 |
| S8 | Commander :3100 | `*:3100` | claude.ai browser sessions via cloudflared | `FLEET_SERVICE_ATLAS.md:35,85` | T4 only | none directly, **but T4 is the Commander pipe**. A 7844 block locks out remote repair over MCP |
| S9 | memory-mcp :3104 | `127.0.0.1:3104` | MCP gateway | `FLEET_SERVICE_ATLAS.md:36` | loopback | none |

### 2.3 LAN-native flows (these bypass Tailscale; they break if the Topton routes between fleet segments)

The ticket's list does not include these, but they are real and a VLAN split would cut them:

| # | flow | proto/port | evidence | severed by |
|---|---|---|---|---|
| L1 | Ma'at ← Wadjet Postgres streaming replication (Vaultwarden HA) | TCP 5432 to `192.168.8.173` from `192.168.8.202` | `bin/wadjet-ha-failback.sh:134-148`; `FLEET_SERVICE_ATLAS.md:90` | inter-VLAN deny. A broken standby is invisible until failover, and failover is the one time it is needed |
| L2 | Home Assistant API | TCP 8123 to `192.168.8.177` | `bin/eplug-fetch.py:12`; `FLEET_SERVICE_ATLAS.md:88` | IoT-VLAN isolation (the usual OPNsense first move) |
| L3 | Tapo P115 plug fleet (13 plugs, `192.168.8.x`) | python-kasa KLAP unicast (vendor HTTP transport) | `bin/plug-ctl:2,8-12` | IoT-VLAN isolation. **This also severs `power-remediate`'s AC-cycle stage**, the fleet's last-resort self-recovery |
| L4 | Wake-on-LAN | UDP broadcast (`wakeonlan`, default UDP 9 to 255.255.255.255) | `bin/power-remediate:257-266` | any L3 boundary, since broadcasts don't route. Use OPNsense's WoL service, or keep power-remediate on the same segment as the nodes it wakes |
| L5 | Bes VictoriaMetrics/VictoriaLogs/MQTT, Thoth media origin | via SSH tunnel / autossh | `FLEET_SERVICE_ATLAS.md:88-89` | covered by T5 |

**Current topology, for context.** Every LAN address in `bin/` is in one `192.168.8.0/24` (`plug-ctl`, `wadjet-ha-failback.sh`, `eplug-fetch.py`, `power-remediate:481`), so today the flat LAN puts L1-L4 on one broadcast domain. If the Topton only replaces the WAN edge and keeps that /24 flat, §2.3 does not apply and only §2.1 matters.

## 3. IDS/IPS — Alert-Only on the fleet, never Block (`FS L841-847`, R34)

`fleet-search` fires bursts of queries to SearXNG, which fans each one out to Google, Bing and DDG. On top of that come `read` fetches through trafilatura and FlareSolverr's headless Chromium (`bin/fleet-search:10-12,44`). **That is scraping.** Suricata's scan/policy/rate-based categories, and Zenarmor's bot heuristics, are built to catch this traffic, and in IPS mode they drop it mid-flight. The result is the worst failure available: a fail-soft tool returning partial results that look complete (`bin/fleet-search:21`).

| setting | value | why |
|---|---|---|
| Suricata/Zenarmor on the fleet hosts/segment | **Alert-Only** (policy action = alert, not drop) | keeps the visibility and never kills a fetch or an LLM stream |
| scope | fleet static IPs + `100.64.0.0/10` | same identity problem as P3 |
| QUIC (UDP 443/7844) | do not block "unknown QUIC" on fleet hosts | cloudflared uses quic (T4). A half-dropped QUIC session is worse than a clean refusal |
| after enabling IDS in any mode | re-run the §4 probes and read the alert log for fleet sources | converting alerts to drops later must be a deliberate, per-rule decision |

`FS L263` also suggests Zenarmor with local Elasticsearch on the firewall. The digest's R10 verdict still stands: defer, because of the cost.

## 4. Pre-deployment checklist (Topton / RPi)

Run each check from a fleet node (e.g. `ssh lumen@<lumen-ip> '<cmd>'`, IP from `lumen_state.py current lumen`) **before** cutover. Do the post-cutover run inside the same maintenance window, with the old router still cabled for rollback.

- [ ] Decide whether the Topton is **edge-only** (flat /24 kept, so §2.3 is moot) or **segmenting** (all of §2.3 applies). Record the decision.
- [ ] Hybrid outbound NAT + Static Port for fleet hosts + NAT-PMP (T3). After cutover, `tailscale status` shows `direct` (not `relay`) for off-LAN peers, and `tailscale netcheck` reports UDP true.
- [ ] Egress pass: UDP 41641, UDP 3478, TCP 443, TCP+UDP 7844 (T1-T4).
- [ ] Port-53 redirect NAT rule, if any, excludes fleet hosts (T6).
- [ ] Pi-hole: either fleet resolvers stay untouched (preferred), or `Fleet-Bypass` is set up per §1.2 with P1-P4 each checked.
- [ ] Every D1-D14 domain resolves from a fleet node after cutover (`getent hosts <domain>` or `resolvectl query <domain>`), and `/home/lumen/bin/fleet-search web "test"` returns `ok:true` with >0 results from ≥2 engines.
- [ ] `cloudflared` reports 4 connections, and a public `*.lumennode.uk` hostname plus Commander load from off-LAN.
- [ ] If segmenting: pass L1 (5432 .202→.173), L2 (8123), L3 (plug fleet) and a WoL relay (L4), then prove `plug-ctl all state` and a Ma'at `pg_is_in_recovery()` = `t`.
- [ ] IDS/IPS in Alert-Only for the fleet scope (§3). A 5-minute fleet-search burst produces zero drops.
- [ ] Rollback: the old router config is saved and the swap-back steps are written down before cutover.

## 5. What this does not cover

- Lumen's own host firewall (`iptables-restore`, `FLEET_SERVICE_ATLAS.md:31`) is a separate layer, not changed by an edge box.
- Only Lumen's listeners were probed. Other nodes' binds were not, deliberately: the service consumers above address them by Tailscale IP (S2), which is the property that matters. Re-probe with `ss -ltnu` on each node as part of the §4 cutover window.
- The D-table is the known set, not a closed list (§1.1 preamble).
