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
- **Described + prior-art knowledge:** OpenClaw, VRAX, ReCopilot, reverse-skill, Recon-2026
  SQL-RE, and the GhidraMCP fork tool-counts (13bm/bethington). For these the feature set
  is taken from the ticket brief and general knowledge of the category; claims about them
  are marked *(described)* and should be re-verified when search is restored before any are
  used to justify a build commitment.

Limpet facts below are cited to the committed design docs, not restated from memory.

## 1. Platform-by-platform

### (1) OpenClaw — general agent platform with a plugin marketplace *(described)*
340K+ stars, multi-agent orchestration, 138+ MCP tools, ClawHub plugin marketplace,
Composio 500+ app integrations, browser MCP, enterprise controls. This is the
*general-purpose coding/agent* platform category (the OpenHands/Composio shape), not an RE
tool. Relevance to Limpet is **orchestration and plugin distribution**, not RE depth.

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
already on the fleet (`TOOL_REGISTRY.md:33-56`).

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

### (4) VRAX — multi-agent swarm binary analysis + live operator console *(described)*
Swarm topology over binary analysis with a live console. Two takeaways: (a) a **live
operator console** — a real-time view of what the agent is doing on the box — which Limpet
lacks (we have a replayable git trail but no live view). **Gap G5.** (b) swarm-over-one-
binary parallelism; lower priority for an appliance with an i5/32GB ceiling
(`design/wq3806_limpet_architecture.md:3,152-153`) where resident-memory pressure, not
agent count, is the constraint.

### (5) ReCopilot — domain-specific binary-analysis LLM *(described)*
Function-name recovery, variable type/name inference — a model *fine-tuned on decompiled
code*, not a general chat model. **Yes, Limpet should be able to mount specialized RE models
alongside the general LLM.** Our agent core already routes via LiteLLM and does not hard-code
one model (`design/wq3806_limpet_architecture.md:233-239`); the clean extension is a
*model-capability tier* in the registry so a decompiler-naming step can be routed to a
ReCopilot-class model while reasoning stays on the general model. **Adopt as A5** (design-only;
no such model is provisioned on the box today — the 32GB/iGPU ceiling means a specialized
model is an opt-in or a fleet-routed call, same policy as the 14B coder model, `:233-239`).

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
- **13bm/GhidraMCP ≈70 tools, bethington/ghidra-mcp ≈209 tools *(described counts)*:** forks
  expanding the tool surface (structs/types, data-flow, scripting bridge).
- **Ours (the fleet `ghidra-mcp` + `ghidra-re` skill):** Ghidra headless already a `core`
  plugin (`design/wq3806_limpet_architecture.md:96-99`), and the plugin examples carry a
  `ghidra-mcp.toml` depending on `ghidra` (`design/wq3810_limpet_plugin_system.md:81-86`).
  Limpet does not need to pick one fork: the registry can carry whichever Ghidra-MCP a
  workspace needs as a `vetted` plugin, and the meta-server namespaces it. The lesson from
  the 209-tool fork is **tool-schema budget** — which is precisely why our meta-server does
  two-level exposure (`design/wq3806_limpet_architecture.md:152-159`) rather than mounting
  200 Ghidra tools flat. No gap; design already absorbs it.

### (10) EMBA / FACT / OFRAK — automated firmware platforms (verified)
- **EMBA (`e-m-b-a/emba`):** central firmware analysis + SBOM; extraction→static→dynamic
  (emulation)→SBOM→web vuln report; auto-discovers weak binaries, outdated components,
  hard-coded passwords. A full *pipeline*, CLI with a web report.
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

## 2. Feature comparison matrix

Legend: ✅ have / ◑ partial / ✗ lack / — out of lane. "Limpet" = the committed design.

| Capability | Limpet | OpenClaw | REA(morluto) | REA(Rust) | VRAX | Kali | EMBA | FACT | OFRAK | Ghidra-MCP |
|---|---|---|---|---|---|---|---|---|---|---|
| One-endpoint tool orchestration | ✅ | ✅ | ✅ | ◑ | ◑ | ✗ | — | — | ◑ | ✗ |
| Large tool surface (100+) | ◑(via promote) | ✅ | ✅ | ✗ | ◑ | ✅ | ◑ | ◑ | ◑ | ◑(209 fork) |
| Trust-tiered plugin gate | ✅ | ◑ | ✗ | — | ✗ | ◑(distro) | ✗ | ✗ | ✗ | ✗ |
| Per-investigation workspace | ✅ | ✗ | ◑(bundle) | ◑(files) | ◑ | ✗ | ◑ | ✅(db) | ◑ | ✗ |
| Per-tool-call evidence/provenance | ◑ | ✗ | ✅ | ✅ | ◑ | ✗ | ◑ | ✅ | ◑ | ✗ |
| Reconstruction obligation ledger | ✗ | ✗ | ✅ | ◑ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Adversarial review agent | ✗ | ◑ | ◑ | ✅ | ✅ | — | — | — | — | — |
| Specialized RE model routing | ◑(design) | ◑ | ✗ | ◑ | ✗ | — | ✗ | ✗ | ✗ | ✗ |
| Firmware extraction | ✅ | ✗ | ◑ | ✅ | ✗ | ◑ | ✅ | ✅ | ✅ | ✗ |
| Binary modify / repack | ✗ | ✗ | ✗ | ✗ | ✗ | ◑ | ✗ | ✗ | ✅ | ✗ |
| Firmware version diff/compare | ◑(wq3906) | ✗ | ◑ | ◑ | ✗ | ✗ | ◑ | ✅ | ◑ | ✗ |
| Live operator console | ✗ | ✅ | ✗ | ✗ | ✅ | — | ◑(web) | ✅(web) | ✗ | ✗ |
| SQL/queryable RE index | ✗ | ✗ | ◑ | ◑ | ✗ | ✗ | ◑ | ✅ | ◑ | ✗ |
| Outbound integration (ticket/chat) | ✗ | ✅ | ✗ | ✗ | ✗ | — | ✗ | ◑(API) | ✗ | ✗ |
| **Physical hardware awareness (HAL)** | ✅ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **Plug-a-device appliance form factor** | ✅ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Local/offline inference | ✅ | ◑ | ✗(cloud) | ✗(OpenRouter) | ✗ | — | ✅ | ✅ | ✅ | ✗ |

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
- **G5 — Live operator console** (VRAX, OpenClaw, FACT). Replayable trail but no live view.
  *Medium; a read-only tail of the workspace evidence log is the cheap version.*
- **G4 — Outbound integrations** (OpenClaw/Composio). Evidence bundle exists but no connector
  to ship it. *Medium; one connector (file/email of the bundle) covers 80%.*
- **G6 — Narrow tool-lane additions** (Kali): tshark + a fuzzer as `core`. *Low; everything
  else in Kali is deliberately out of lane.*
- **G8 — Manifest signing/trust-at-scale** (Kali curation). Already flagged future
  (`design/wq3810_limpet_plugin_system.md:118-121`); needed before any `community` auto-clone.

## 4. Differentiator validation (do our unique advantages hold?)

Checked against every platform above — these are the columns where Limpet is the *only* ✅:

- **Physical hardware awareness (HAL).** No software-only peer (REA, OpenClaw, EMBA, FACT,
  OFRAK, GhidraMCP) touches physical probes/capture. Limpet's udev-driven `list_hardware`,
  OpenOCD debug-probe, and uvcvideo screen-capture classes
  (`design/wq3806_limpet_architecture.md:180-204`) are genuinely unmatched. **HOLDS.**
- **Plug-a-device appliance form factor.** Every peer is software you install; Limpet is a
  box you plug a device into (`design/wq3806_limpet_architecture.md:3-7`). **HOLDS** — and it
  is the brand position Kali doesn't occupy (§1.6).
- **Trust-tiered, drop-in plugin gate with per-plugin git provenance.** OpenClaw/Kali have
  marketplaces/distros but not the install-decoupled-from-enablement, fail-closed,
  hash-pinned gate (`design/wq3806_limpet_architecture.md:71-88,161-178`;
  `design/wq3810_limpet_plugin_system.md:43-66`). This is a real differentiator *and* the
  direct answer to the astroturf-adoption threat that put REA on hold
  (`design/wq3806_limpet_architecture.md:40-48`). **HOLDS.**
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
- **Re-verify when search is restored:** every *(described)* platform (OpenClaw, VRAX,
  ReCopilot, reverse-skill, Recon-SQL, the 13bm/bethington tool counts) before any of A3/A5/
  A6 is turned into a build commitment (ISSUE-LOG #1325 records why they are described, not
  verified).
