# wq#3929 — LIMPET competitive analysis: the AI-driven RE landscape

Status: research/design. Author: build worker, 2026-10-15. Blocks: remaining Limpet
design work (we need the baseline users expect before fixing scope).

This document answers one question: **does Limpet's design (as it stands in
`design/wq3806_limpet_architecture.md` and `design/wq3810_limpet_plugin_system.md`)
match or exceed what an operator can already get elsewhere, and where are the gaps?**
It is a comparison against the live landscape, not a wish-list.

## 0. Method and grounding (what is verified vs. described)

Doctrine is prior-art-first. The fleet web/github/reddit search backends were all down at
dispatch (SearXNG general engines suspended/CAPTCHA, no `BRAVE_API_KEY` per wq#2595, GitHub
API unauthenticated — logged ISSUE-LOG #1325). The working backends were used instead:
`fleet-search read <url>` (fetch+extract) and `hn`/`so`. So the grounding tiers are:

- **Verified live (repo pages fetched 2026-10-15):** REA (morluto / npm `rea-agents`) —
  also installed on the fleet, `mcp__rea__*` live; REA (mytechnotalent/rea, pure-Rust);
  EMBA (`e-m-b-a/emba`); OFRAK (`redballoonsecurity/ofrak`); FACT (`fkie-cad/FACT_core`);
  GhidraMCP (`LaurieWired/GhidraMCP`).
- **Verified live (independent web search, 2026-10-14):** the entities the first pass could
  only mark *(described)* — OpenClaw, VRAX, ReCopilot, reverse-skill, Recon-2026 SQL-RE, and
  the GhidraMCP fork tool-counts (13bm/bethington) — were all confirmed REAL by an
  out-of-fleet live-search pass (browser Claude, `/home/lumen/scratch/research/limpet_live_search_2026-10-14.md`,
  PART A). **The re-verify caveat is lifted.** That pass also corrected several figures
  (OpenClaw ~180K users not 340K stars; bethington/ghidra-mcp 244–256 tools not ≈209) and
  downgraded VRAX to a described architecture (single LinkedIn source, no public repo) — the
  corrections are folded into §1 below and the matrix. The *(verified live)* tag marks each.

Limpet facts below are cited to the committed design docs, not restated from memory.

## 1. Platform-by-platform

### (1) OpenClaw — general agent platform with a plugin marketplace *(verified live, wq#3995)*
~180K users (corrected from the brief's "340K+ stars"), multi-agent orchestration, 138+ MCP
tools, ClawHub plugin marketplace, Composio 500+ app integrations, browser MCP, enterprise
controls. Live search (`.../limpet_live_search_2026-10-14.md` A0) confirms this is a
*general-purpose* AI agent platform (the OpenHands/Composio shape), **not RE-specific**, so
relevance to Limpet is **orchestration and plugin distribution**, not RE depth.

- **Orchestration vs. our meta-server.** OpenClaw orchestrates *agents*; Limpet's
  MCP meta-server orchestrates *tools* behind one endpoint with lazy child-spawn and a
  two-level router surface (`design/wq3806_limpet_architecture.md:112-159`). These are
  complementary layers, not competitors: Limpet is the tool-plane an OpenClaw-style agent
  could mount. The thing to learn is their **two-level tool exposure under schema budget** —
  which our design already adopts (`:152-159`).
- **ClawHub plugins we should learn from:** a browser-MCP capability (drive a device's web
  admin UI), and app-integration connectors (ticketing/chat) for shipping an evidence
  bundle out. Limpet has the evidence bundle (`design/wq3806_limpet_architecture.md:227-229`)
  but no outbound connector yet — gap G4.
- **Enterprise controls** (RBAC, audit, policy) map onto our trust-tier + per-workspace
  audit log (`design/wq3806_limpet_architecture.md:71-88,148-149`). We are at parity in
  intent; they are ahead in maturity (SSO/multi-tenant).

### (2) REA — morluto / npm `rea-agents` (verified: installed, `mcp__rea__*` live)
~170 tools (skill v37), investigation model, evidence tracking, CLI + npm, Ghidra provider
(Hopper/IDA optional). This is the **closest peer to Limpet's software-tool plane** and is
already on the fleet (`TOOL_REGISTRY.md:33-56`). Live search (`.../limpet_live_search_2026-10-14.md`
A0) adds two boundary facts: REA is **macOS-centric** (Homebrew/Hopper, "check your Mac") and
**explicitly never invokes the privileged-escalation path** (a documented mutation boundary).
Both **strengthen Limpet's Linux-appliance + HAL wedge**: the closest software peer is a Mac
dev tool with no hardware plane, not a hardware-aware box.

- **Their evidence model vs. our workspace manager.** REA emits inline per-tool *Evidence*
  with provenance/`untrusted_content` marking, and has `export_evidence_bundle` /
  `import_evidence_bundle`. Limpet's workspace manager is a superset in *persistence*: one
  git-backed workspace per investigation, append-only `evidence/`, replayable commit trail
  (`design/wq3806_limpet_architecture.md:206-229`). REA is a superset in *per-result
  evidence granularity* — every tool return carries a coverage/limitations note. **Gap G1:
  Limpet should adopt per-tool-call evidence metadata (provenance + coverage), not just a
  workspace-level log.** Our design already commits to mapping onto REA's bundle format
  (`:229`), which de-risks this.
- **Investigation patterns we don't have:** REA's `build_reconstruction_obligation_ledger`
  / `evaluate_reconstruction_coverage` / `verify_reconstruction` loop — a *coverage ledger*
  that tracks what still has to be explained before an RE claim is "done." Limpet's agent
  loop has verify-as-a-step (`design/wq3806_limpet_architecture.md:240-245`) but no explicit
  obligation ledger. **Gap G2** — this is the single most valuable pattern to adopt; it is
  the RE analogue of the fleet's own evidence gate.
- **Runtime+static correlation** (`correlate_static_and_runtime`, Frida/GDB/rizin sessions,
  ADB/APK, web/Electron capture) is a very broad surface. Limpet reaches this honestly only
  by promoting REA through the TOOL_REGISTRY gate (`design/wq3806_limpet_architecture.md:100-110`),
  which is the designed path — not a gap, a dependency.

### (3) REA — mytechnotalent/rea (verified: pure-Rust, multi-agent)
"Run anywhere, nothing installed, every claim verified." `rea <file>` →
`re-suspect.{md,pdf,json}`. Hybrid autonomous multi-agent reasoning + a deterministic
engine; Rust firmware extraction; OpenRouter API; PDF dossier.

- **Multi-agent topology vs. ours.** Their propose→review→adjudicate loop with a
  *deterministic engine* backing the LLM agents is exactly the fleet's own
  propose/critic/adjudicate posture. Limpet's agent core is currently single-loop
  (identify→toolset→orchestrate→verify, `design/wq3806_limpet_architecture.md:240-245`).
  **Gap G3: Limpet has no adversarial review agent in the analysis loop.** Adopting a
  deterministic-engine-checks-LLM split is high value and matches house doctrine.
- **Firmware extraction pipeline.** Theirs is pure-Rust and self-contained. Limpet's is the
  fleet skill stack (unblob→binwalk, `firmware-extraction`) wrapped as `core` plugins
  (`design/wq3806_limpet_architecture.md:96-99`). unblob/binwalk coverage exceeds a bespoke
  Rust extractor; **keep ours**, but steal their "install nothing, one command, verifiable
  dossier" packaging as the operator UX target.
- **PDF dossier.** We have `firmware-security-reports` (md+PDF) already as a `core` skill;
  near-parity. Make dossier generation a first-class workspace export alongside the evidence
  bundle.

### (4) VRAX — multi-agent swarm binary analysis + live operator console *(DOWNGRADED — described architecture, not a shipping competitor)*
Swarm topology over binary analysis with a live console. **Downgrade (wq#3995):** live search
(`.../limpet_live_search_2026-10-14.md` A0) found the *only* substantive source is a single
LinkedIn profile (Chathuranga BW, UAE) — no public repo, release, or community. So VRAX is a
**described architecture, not a shipping competitor**; treat it as evidence that others pursue
the pattern, not as a product to benchmark against. Two takeaways survive the downgrade: (a) a
**live operator console** — a real-time view of what the agent is doing on the box — which
Limpet lacks (we have a replayable git trail but no live view). **Gap G5** stays a validated
pattern (EMBArk, below, is the verified prior art for it). (b) swarm-over-one-
binary parallelism; lower priority for an appliance with an i5/32GB ceiling
(`design/wq3806_limpet_architecture.md:3,152-153`) where resident-memory pressure, not
agent count, is the constraint.

### (5) ReCopilot — domain-specific binary-analysis LLM *(verified live, wq#3995)*
Function-name recovery, variable type/name inference, struct recovery, SimAI code-homology
search — a model *fine-tuned on decompiled code* (arXiv 2505.16366, SOTA +13%), not a general
chat model, shipped as an IDA Pro plugin. **Yes, Limpet should be able to mount specialized RE
models alongside the general LLM.** Our agent core already routes via LiteLLM and does not
hard-code one model (`design/wq3806_limpet_architecture.md:233-239`); the clean extension is a
*model-capability tier* in the registry so a decompiler-naming step can be routed to a
ReCopilot-class model while reasoning stays on the general model. **Adopt as A5** (design-only;
no such model is provisioned on the box today — the 32GB/iGPU ceiling means a specialized
model is an opt-in or a fleet-routed call, same policy as the 14B coder model, `:233-239`).
**Data-egress caveat (wq#3995):** the published `recopilot-v0.1-beta-dpo` weights are served
from a **China-hosted API (XingTuLab / QI-ANXIN, `tqgpt.qianxin.com`)**, and self-hosting is
infeasible on the box. So an A5 route to ReCopilot-as-a-service would egress decompiled target
code to a third-party foreign endpoint — out of policy for a sensitive investigation. The
adopt is the *capability-tier mechanism*, not a default route to this specific provider.

### (6) Kali Linux — tool distribution + brand *(partly described)*
Kali is the brand baseline operators compare any security appliance to. Two lenses:

- **Tools they ship that Limpet doesn't (as `core`):** the Limpet `core` seed is firmware/RE
  focused — unblob, binwalk, Ghidra, QEMU, gdb-multiarch, OpenOCD, radare2
  (`design/wq3806_limpet_architecture.md:96-99`) plus the verified Maksoor inventory
  (frida, flashrom, rkdeveloptool, mtkclient, heimdall, cutter, jadx, apktool, sigrok —
  `TOOL_REGISTRY.md:15-30`). Kali adds the broader offensive surface: network (nmap,
  wireshark, aircrack-ng), web (burp/zap), fuzzing (afl++), exploitation (metasploit),
  password (hashcat/john). Most are **out of Limpet's RE lane by design** — do not chase
  parity. The defensible overlaps to add as `core`: **wireshark/tshark** (pairs with the HAL
  network-adapter and SDR classes, `design/wq3806_limpet_architecture.md:197-201`) and
  **afl++/a fuzzer** for the dynamic-analysis step. **Gap G6** (narrow).
- **Packaging vs. our plugin system.** Kali = a curated apt distribution (kali-meta
  packages); Limpet = drop-in `limpet-plugin.toml` + registry daemon
  (`design/wq3810_limpet_plugin_system.md:15-31`). Our model is better for *third-party
  extension* (no distro gatekeeper, per-plugin git provenance, `wq3810:80-86`) and weaker
  for *curated trust-at-scale* (Kali's maintainers vet every package). Our trust-tier split
  (`design/wq3806_limpet_architecture.md:71-88`) is the answer to that, but it needs the
  signature/trust-on-manifest work flagged as future (`design/wq3810_limpet_plugin_system.md:118-121`).
- **Brand positioning.** Kali owns "the offensive Linux distro." Limpet's unoccupied
  position is **"the RE appliance you plug a device into"** — a hardware-aware, single-target
  workspace box, not a distro. That is the differentiator to lead with (§4).

### (7) reverse-skill — scenario-based skill routing *(described)*
Routes to a skill by *scenario* ("this is an APK" → the APK skill set). Limpet's meta-server
already does target-class toolset expansion (`design/wq3806_limpet_architecture.md:152-159`):
the agent asks for the firmware-vs-APK-vs-web toolset and only those schemas mount. The
pattern worth adopting is making the **target-class → toolset map a declared, inspectable
table** (like reverse-skill's routing table) rather than agent-chosen each time — cheaper,
auditable, and it seeds the two-level router. **Adopt as A4** (low cost, aligns with existing
design).

### (8) Recon-2026 SQL-based RE — SQL as a universal disassembler query language *(described)*
SQL over disassembly/symbols/xrefs as one query interface across tools. This is attractive:
it is exactly Limpet's "write evidence to the workspace and *reference* it" need
(`design/wq3806_limpet_architecture.md:144-147`) turned into a queryable store. **Should
Limpet support it? Yes, as an optional workspace index, not the primary interface.** A
per-workspace SQLite of {functions, strings, xrefs, symbols, tool-invocations} fits the
existing `workspace.git`/`manifest.json` model (`:211-218`) and gives the agent a cheap way
to query large RE output without inlining megabytes into context. **Adopt as A6** (bounded:
an index beside the evidence, not a rewrite of the tool plane).

### (9) GhidraMCP variants — feature/tool-count comparison
- **LaurieWired/GhidraMCP (verified):** MCP server + Ghidra plugin; decompile, auto-rename
  methods/data, list methods/classes/imports/exports. Compact, well-known, the reference
  implementation.
- **13bm/GhidraMCP ≈70 tools, bethington/ghidra-mcp 244–256 tools (verified live, wq#3995):**
  forks expanding the tool surface (structs/types, data-flow, scripting bridge). Live search
  (`.../limpet_live_search_2026-10-14.md` A2) corrected bethington **up** from the offline
  "≈209" to **244–256 tools** (v5.14.2, 2026-06-27; 1.8k★; headless + Docker + Ghidra-Server
  multiuser, P-code emulation, live debugger, PCode-graph dataflow).
- **Ours (the fleet `ghidra-mcp` + `ghidra-re` skill):** Ghidra headless already a `core`
  plugin (`design/wq3806_limpet_architecture.md:96-99`), and the plugin examples carry a
  `ghidra-mcp.toml` depending on `ghidra` (`design/wq3810_limpet_plugin_system.md:81-86`).
  Limpet does not need to pick one fork: the registry can carry whichever Ghidra-MCP a
  workspace needs as a `vetted` plugin, and the meta-server namespaces it. The lesson from
  the 244–256-tool fork is **tool-schema budget** — which is precisely why our meta-server
  does two-level exposure (`design/wq3806_limpet_architecture.md:152-159`) rather than mounting
  250 Ghidra tools flat. **Strengthened (wq#3995):** bethington/ghidra-mcp now advertises
  **"lazy tool loading"** — a competitor has *independently converged* on the exact
  schema-budget answer Limpet's two-level meta-server adopts. Two consequences: (1) raw
  tool-count is **confirmed not a moat** (§4 caveat holds, strengthened); (2) two-level/lazy
  tool exposure is now **table stakes, not a differentiator** — Limpet's edge there is the
  trust-tier gate around it, not the lazy-load itself. (Also live: MCPyGhidra — Nightwing, 26
  tools, +binary patching; Sarks0/binary-mcp — Ghidra static 22 + x64dbg dynamic 14 +
  cross-conversation sessions, close to Limpet's workspace continuity.) No gap; design absorbs it.

### (10) EMBA / FACT / OFRAK — automated firmware platforms (verified)
- **EMBA (`e-m-b-a/emba`):** central firmware analysis + SBOM; extraction→static→dynamic
  (emulation)→SBOM→web vuln report; auto-discovers weak binaries, outdated components,
  hard-coded passwords. A full *pipeline*, CLI with a web report. **More current than the
  offline doc (verified live, wq#3995):** EMBA now ships **AI support**, **firmware diffing**,
  UEFI analysis (FwHunt/Binarly), user+system emulation, and **EMBArk — a multi-user team web
  console**. Two consequences: its **firmware diffing overlaps Limpet's wq#3906 diff work**
  (align, do not duplicate — see §1.10 note below), and **EMBArk is verified prior art for the
  live-operator-console gap (G5)** now that VRAX is downgraded.
- **FACT (`fkie-cad/FACT_core`):** automates firmware analysis with browsable/searchable/
  **comparable** results, web UI + REST API/SwaggerUI, plugin analyses.
- **OFRAK (`redballoonsecurity/ofrak`):** unpack → analyze → **modify & repack** with
  patching strategies; a programmable platform (Python), not just a report.

How they compare and integrate with Limpet:
- These are **batch/pipeline** tools (point at an image, get a report); Limpet is
  **interactive/agentic** (an agent drives tools against one workspace with hardware
  attached). They are not competitors so much as **tools Limpet should wrap as `core`/`vetted`
  plugins** and let the agent invoke — EMBA/FACT as a "run the automated firmware pass and
  ingest its findings into the workspace," OFRAK as the **modify-and-repack** capability
  Limpet otherwise lacks. **Gap G7: Limpet has no binary modification/repack capability** —
  our design is analysis-only (`design/wq3806_limpet_architecture.md:240-245`). OFRAK fills
  it and is the natural adopt.
- FACT's **comparable results** (diff two firmware versions) overlaps our own
  `design/wq3906_limpet_firmware_diff.md` work — align them rather than duplicate.
- Their SBOM/vuln-report output is the kind of deliverable our evidence bundle should be able
  to carry (`design/wq3806_limpet_architecture.md:227-229`).

### (11) AttifyOS — pre-configured IoT / hardware-RE Linux distro (verified live, wq#3994)

AttifyOS (Attify, Inc., commercial) is a pre-configured IoT/hardware-RE Linux distribution
bundling firmware extraction + hardware interfaces (UART/JTAG/SPI/I2C) + SDR, built on the
assumption of hands-on hardware access. Live search (orch-limpet-research,
`/home/lumen/scratch/research/limpet_live_search_2026-10-14.md` PART A1) surfaced it as the
offline doc's **most important omission**: it is the **nearest prior art to Limpet's
"hardware-aware RE distro / plug-a-device" positioning — closer than Kali (§1.6)**.

- **Correction to the brand claim.** Limpet is **not** "the first hardware-aware RE distro" —
  AttifyOS already bundles the same probe/capture tool classes Limpet's HAL exposes
  (`design/wq3806_limpet_architecture.md:180-204`). What AttifyOS is *not*: not agent-driven,
  not auto-detecting (the operator wires and configures interfaces by hand), and not an
  appliance (it is an image you install, like Kali). **Limpet's defensible wedge is therefore
  the intersection AttifyOS lacks: agent-driven + auto-detecting HAL + appliance form factor**
  — see the re-worded §4.
- **Related legacy tool.** `attify/firmware-analysis-toolkit` (1,549★, MIT) is a flat
  Firmadyne wrapper, now legacy; the active successor is the FAT2.0 fork already tracked by
  wq#3959. No separate matrix column — folded into the firmware-extraction lane.

### (12) REx@Skill — agentic binary-vuln skill with a defect-proof schema (verified live, wq#3994)

`tihanyin/REx-skill` is an agentic RE skill for binary-vulnerability discovery over Ghidra +
capa. It is a **direct peer to Limpet's workspace + evidence model**, and two of its
conventions are worth adopting outright:

- **sha256-named workspace.** REx keeps one directory per binary named
  `<filename>-<sha256[:8]>`, so two builds of the same name never collide. Limpet's workspace
  manager is "one git-backed workspace per investigation"
  (`design/wq3806_limpet_architecture.md:206-229`) but does not pin the directory name to
  artifact content. **Adopt the sha256-named-workspace convention** (A12 below) — it makes a
  workspace content-addressable and collision-free, matching the content-over-flag trust
  thesis the plugin-signing work relies on.
- **Four-part defect-proof schema.** A bug is reported only when **source + sink +
  broken-guard + affected-principal** are all named and located. This is a concrete, auditable
  shape for the reconstruction obligation ledger (G2): the ledger's open obligations can be
  typed against these four slots. **Adopt as the defect-proof template** inside A1→G2.

### (13) Academic RE-agent benchmarks — CrackMeBench / AgentRE-Bench (verified live, wq#3994)

Live search surfaced published agentic-RE evaluation instruments: **CrackMeBench**
(arXiv 2605.10597), **AgentRE-Bench** (agentre-bench.ai), and the survey "Challenges and
Future Directions in Agentic Reverse Engineering Systems" (arXiv 2604.14317). These are not
tools to wrap but **eval instruments to measure against**: adopting a published benchmark is
cheap external credibility and a forcing function, and ties to fleet golden-set doctrine.
**Adopt as A13** (design-only: pick one benchmark as Limpet's standing eval). Not a
capability column — benchmarks are measurement, not features.

## 2. Feature comparison matrix

Legend: ✅ have / ◑ partial / ✗ lack / — out of lane. "Limpet" = the committed design.

Columns now include **AttifyOS** (the hardware-RE distro, §1.11) and **REx@Skill** (§1.12).
AttifyOS is the one peer that is *not* ✗ on bundled hardware interfaces — which is exactly why
the HAL/appliance differentiator is re-worded in §4.

| Capability | Limpet | OpenClaw | REA(morluto) | REA(Rust) | VRAX | Kali | AttifyOS | EMBA | FACT | OFRAK | REx@Skill | Ghidra-MCP |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| One-endpoint tool orchestration | ✅ | ✅ | ✅ | ◑ | ◑ | ✗ | ✗ | — | — | ◑ | ◑ | ✗ |
| Large tool surface (100+) | ◑(via promote) | ✅ | ✅ | ✗ | ◑ | ✅ | ✅(distro) | ◑ | ◑ | ◑ | ✗ | ◑(244–256 fork) |
| Trust-tiered plugin gate | ✅ | ◑ | ✗ | — | ✗ | ◑(distro) | ◑(distro) | ✗ | ✗ | ✗ | ✗ | ✗ |
| Per-investigation workspace | ✅ | ✗ | ◑(bundle) | ◑(files) | ◑ | ✗ | ✗ | ◑ | ✅(db) | ◑ | ✅(sha256 dir) | ✗ |
| Per-tool-call evidence/provenance | ◑ | ✗ | ✅ | ✅ | ◑ | ✗ | ✗ | ◑ | ✅ | ◑ | ✅(4-part) | ✗ |
| Reconstruction obligation ledger | ✗ | ✗ | ✅ | ◑ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ◑(defect schema) | ✗ |
| Adversarial review agent | ✗ | ◑ | ◑ | ✅ | ✅ | — | — | — | — | — | ◑ | — |
| Agent-driven (vs. manual toolkit) | ✅ | ✅ | ✅ | ✅ | ✅ | ✗ | ✗ | ✗ | ✗ | ✗ | ✅ | ◑ |
| Specialized RE model routing | ◑(design) | ◑ | ✗ | ◑ | ✗ | — | — | ✗ | ✗ | ✗ | ✗ | ✗ |
| Firmware extraction | ✅ | ✗ | ◑ | ✅ | ✗ | ◑ | ✅ | ✅ | ✅ | ✅ | ✗ | ✗ |
| Binary modify / repack | ✗ | ✗ | ✗ | ✗ | ✗ | ◑ | ✗ | ✗ | ✗ | ✅ | ✗ | ✗ |
| Firmware version diff/compare | ◑(wq3906) | ✗ | ◑ | ◑ | ✗ | ✗ | ✗ | ◑ | ✅ | ◑ | ✗ | ✗ |
| Live operator console | ✗ | ✅ | ✗ | ✗ | ✅ | — | ✗ | ◑(web) | ✅(web) | ✗ | ✗ | ✗ |
| SQL/queryable RE index | ✗ | ✗ | ◑ | ◑ | ✗ | ✗ | ✗ | ◑ | ✅ | ◑ | ✗ | ✗ |
| Outbound integration (ticket/chat) | ✗ | ✅ | ✗ | ✗ | ✗ | — | ✗ | ✗ | ◑(API) | ✗ | ✗ | ✗ |
| Bundled hardware interfaces (UART/JTAG/SPI/I2C/SDR) | ✅ | ✗ | ✗ | ✗ | ✗ | ◑ | ✅ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **Auto-detecting HAL (plug → identify)** | ✅ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **Plug-a-device appliance form factor** | ✅ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Local/offline inference | ✅ | ◑ | ✗(cloud) | ✗(OpenRouter) | ✗ | — | — | ✅ | ✅ | ✅ | ◑ | ✗ |

## 3. Gap list (what competitors have that Limpet doesn't)

Ordered by value. "Gap" = a capability a peer ships that Limpet's committed design lacks or
only partially covers.

- **G1 — Per-tool-call evidence metadata** (REA). Workspace log exists; per-result
  provenance/coverage does not. *Cheap, high value, de-risked by the bundle alignment at
  `design/wq3806_limpet_architecture.md:229`.*
- **G2 — Reconstruction obligation ledger** (REA). No explicit "what still must be explained"
  tracker. *The highest-value RE-specific gap; it is the RE analogue of the fleet evidence
  gate.*
- **G3 — Adversarial review agent** (REA-Rust, VRAX). Single-loop agent, no propose/critic
  split in-analysis. *High value, matches house doctrine.*
- **G7 — Binary modify/repack** (OFRAK). Design is analysis-only. *Adopt by wrapping OFRAK;
  do not build.*
- **G5 — Live operator console** (EMBA's EMBArk, OpenClaw, FACT; VRAX downgraded to described,
  §1.4). Replayable trail but no live view. *Medium; a read-only tail of the workspace evidence
  log is the cheap version. Prior art is now verified-shipping (EMBArk), not just VRAX's
  described console.*
- **G4 — Outbound integrations** (OpenClaw/Composio). Evidence bundle exists but no connector
  to ship it. *Medium; one connector (file/email of the bundle) covers 80%.*
- **G6 — Narrow tool-lane additions** (Kali): tshark + a fuzzer as `core`. *Low; everything
  else in Kali is deliberately out of lane.*
- **G8 — Manifest signing/trust-at-scale** (Kali curation). Already flagged future
  (`design/wq3810_limpet_plugin_system.md:118-121`); needed before any `community` auto-clone.
- **G9 — Content-addressed workspace naming** (REx@Skill, §1.12). Limpet keeps one git-backed
  workspace per investigation (`design/wq3806_limpet_architecture.md:206-229`) but does not pin
  the directory name to the target artifact's hash, so two builds of the same filename can
  collide. *Cheap: adopt the `<filename>-<sha256[:8]>` convention (A12).*
- **G10 — Not first in the hardware-RE-distro lane** (AttifyOS, §1.11). Not a capability gap
  but a *positioning* correction: AttifyOS already bundles the probe/capture classes Limpet's
  HAL exposes, so the "first hardware-aware RE distro" claim is false. The response is the
  re-worded §4 differentiator, not a build item.

## 4. Differentiator validation (do our unique advantages hold?)

Checked against every platform above — these are the columns where Limpet is the *only* ✅:

- **Agent-driven + auto-detecting HAL + appliance form factor (the hardware wedge).**
  *Re-worded for wq#3994 — AttifyOS (§1.11) is the incumbent that makes the old "first
  hardware-aware RE distro" framing false.* AttifyOS already bundles the same probe/capture
  tool classes (UART/JTAG/SPI/I2C/SDR) Limpet's HAL exposes
  (`design/wq3806_limpet_architecture.md:180-204`), so bundled hardware access is **not** the
  differentiator. What no peer — AttifyOS included — combines is the three-way intersection:
  (1) **agent-driven** operation rather than a manual toolkit, (2) an **auto-detecting HAL**
  that identifies a device on plug-in via the udev-driven `list_hardware`/OpenOCD/uvcvideo
  classes (`:180-204`) rather than hand-wired interfaces, and (3) a **plug-a-device appliance**
  form factor (`:3-7`) rather than a distro image you install. AttifyOS has none of the three;
  Kali has none; every software-only peer (REA, OpenClaw, EMBA, FACT, OFRAK, GhidraMCP) is ✗
  on all of them. **Lead with this intersection, not with "hardware awareness" alone.** **HOLDS.**
- **Trust-tiered, drop-in plugin gate with per-plugin git provenance.** OpenClaw/Kali have
  marketplaces/distros but not the install-decoupled-from-enablement, fail-closed,
  hash-pinned gate (`design/wq3806_limpet_architecture.md:71-88,161-178`;
  `design/wq3810_limpet_plugin_system.md:43-66`). This is a real differentiator *and* the
  direct answer to the astroturf-adoption threat that put REA on hold
  (`design/wq3806_limpet_architecture.md:40-48`). Live search (A3) gives it concrete evidence:
  researchers found **42,000+ exposed OpenClaw instances plus malicious skills on the ClawHub
  marketplace** — a popular agent plus an unvetted central skill registry equals mass
  compromise, exactly the failure Limpet's trust-tiered, fail-closed, hash-pinned gate is
  built to prevent. **HOLDS.**
- **Git-backed, replayable per-investigation workspace + local/offline inference.** REA has
  bundles; none has a committed, diffable workspace *and* on-box inference. **HOLDS** (local
  inference is constrained by the 32GB/iGPU box — real but bounded, `:233-239`).

Caveat: "147+ tools" is **not** a validated differentiator — every peer rivals or exceeds raw
count, and our own architecture explicitly treats the number as an outcome of promotion, not
a claim (`design/wq3806_limpet_architecture.md:109-110,269-270`). Lead with the appliance +
HAL + trust gate, not the count.

## 5. Prioritized features to adopt

Ranked by (value ÷ cost), each tied to a gap and the existing design hook it extends.

1. **A1 → G2: reconstruction obligation ledger.** Highest value. Add a per-workspace ledger
   of open RE obligations the agent must close before "done," checked by a verify step.
   Extends `design/wq3806_limpet_architecture.md:240-245`. Borrow REA's
   `build_reconstruction_obligation_ledger` shape.
2. **A2 → G1: per-tool-call evidence metadata.** Attach provenance + coverage to every
   meta-server tool return before it hits the workspace log
   (`design/wq3806_limpet_architecture.md:144-149`). Cheap; aligns with the REA bundle format
   we already target (`:229`).
3. **A3 → G3: adversarial review agent.** Add a critic pass (ideally with a deterministic
   check, à la REA-Rust) to the analysis loop. Matches house propose/critic/adjudicate.
4. **A7 → G7: wrap OFRAK as the modify/repack plugin.** Buy, don't build — `vetted`-tier
   plugin via the registry (`design/wq3806_limpet_architecture.md:100-110`).
5. **A5 → ReCopilot: model-capability tier.** Let the registry declare which steps route to a
   specialized RE model vs. the general one (`design/wq3806_limpet_architecture.md:233-239`).
6. **A6 → Recon-SQL: per-workspace SQLite RE index.** Queryable {functions,strings,xrefs,
   symbols,invocations} beside the evidence (`design/wq3806_limpet_architecture.md:211-218`).
7. **A4 → reverse-skill: declared target-class→toolset routing table** feeding the two-level
   router (`design/wq3806_limpet_architecture.md:152-159`).
8. **A8 → G5: read-only live console** = a tail of the workspace evidence log. Low effort.
9. **A9 → G8: manifest signing** before any `community` auto-clone
   (`design/wq3810_limpet_plugin_system.md:118-121`).
10. **A10 → G6: add tshark + a fuzzer to the `core` seed**
    (`design/wq3806_limpet_architecture.md:96-99`). Narrow, do last.
11. **A11 → G4: one outbound evidence-bundle connector.** Lowest priority for an appliance.
12. **A12 → G9: sha256-named workspace** (REx@Skill, §1.12). Name each workspace directory
    `<filename>-<sha256[:8]>` so two builds of the same name never collide and a workspace is
    content-addressable (`design/wq3806_limpet_architecture.md:206-229`). Near-zero cost;
    also carry REx's four-part **source + sink + broken-guard + affected-principal**
    defect-proof schema as the obligation-ledger template (folds into A1→G2).
13. **A13 → benchmarks: adopt a published RE-agent eval** (CrackMeBench / AgentRE-Bench,
    §1.13). Pick one as Limpet's standing eval instrument — cheap external credibility and a
    forcing function, ties to fleet golden-set doctrine. Design-only; no build on the box.

## 6. Unblocking the design work

The baseline users expect, distilled for the downstream design tickets this one blocks:

- **Table stakes (every serious peer has these):** large orchestrated tool surface, firmware
  extraction, per-result evidence, a report/dossier, version-diff. Limpet has all in design;
  A1/A2 close the evidence-quality gap to REA.
- **Winning features users don't get elsewhere (our moat):** plug-a-device HAL, the
  appliance form factor, the trust-tiered drop-in plugin gate, the git-backed workspace.
  These are validated (§4) and are what the brand should lead with.
- **The one thing to stop claiming:** raw tool count as the differentiator. The design
  already says so (`design/wq3806_limpet_architecture.md:269-270`); the marketing/positioning
  must follow it.
- **Re-verification done (wq#3995 — caveat lifted):** the independent live-search pass
  (`/home/lumen/scratch/research/limpet_live_search_2026-10-14.md`, PART A) confirmed every
  entity the first pass could only mark *(described)* — OpenClaw, VRAX, ReCopilot,
  reverse-skill, Recon-SQL, the 13bm/bethington tool counts — is REAL. Net changes carried into
  this doc: OpenClaw ~180K users (not 340K stars); bethington/ghidra-mcp 244–256 tools (not
  ≈209) now shipping lazy tool loading (tool-count confirmed not a moat); VRAX downgraded to a
  described architecture (single LinkedIn source); ReCopilot carries a China-hosted
  data-egress caveat; EMBA adds AI/diffing/EMBArk. A3/A5/A6 are cleared to proceed on verified
  grounding (A5 design-only, subject to the ReCopilot egress caveat).
