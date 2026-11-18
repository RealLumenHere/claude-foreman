# OpenClaw / Grok Bot Ecosystem Dissection — Limpet OS Agent Features

**Ticket:** wq#4922
**Date:** 2026-11-18
**Author:** Fleet worker (automated)
**Purpose:** Evaluate 6 open-source projects from the OpenClaw/Grok Bot ecosystem for features extractable to Limpet OS.

---

## Executive Summary

Six projects were dissected for Limpet-applicable architecture patterns. The highest-value
extractions are: (1) OpenClaw's skills/plugin architecture and 29+ channel system for
Limpet's channel-agnostic agent interface, (2) ZeroClaw's Rust agent runtime for Limpet's
constrained-environment kernel, (3) open-duet/Suparagent's deny-by-default approval gateway
for Limpet agent authority, and (4) Hermes Agent's hybrid sandbox model and self-improving
skill loop for Limpet's flexible execution environments. OpenMausBot's VM-per-bot isolation
maps directly to Limpet's strong agent isolation needs. The Composio security plugin
(OWASP-aligned) provides a reusable security framework for Limpet's agent actions.

Already ticketed and NOT duplicated here: wq#4865 (OpenGrokBot/wolfqing), wq#4860
(Clawboo + Pinchy agent orchestration + workspace isolation).

---

## 1. OpenClaw (openclaw/openclaw)

**URL:** https://github.com/openclaw/openclaw (fetched 2026-11-18)
**Stars:** ~395K | **License:** MIT | **Language:** TypeScript/Rust hybrid
**Foundation:** OpenClaw Foundation, independent 501(c)(3), no paid tier

### Architecture

- **Gateway model:** Trusted gateway (local control plane) + untrusted execution. Policy
  enforced by code, not by trust. Gateway owns sessions, tools, events, channel connections.
  Runs as `openclaw-gateway` daemon (systemd user unit on Linux, launchagent on macOS).
- **Channels (29+):** Discord, iMessage, Slack, Teams, Telegram, WhatsApp, Google Chat,
  Signal, Matrix, email, voice, webhooks, CLI, native apps (macOS, iOS, Android, Windows,
  Linux). One agent answers across all configured channels — channel-agnostic by design.
  Each channel is a plugin adapter to the same Gateway API.
- **Skills architecture:** Human-authored SKILL.md files distributed via ClawHub
  (https://clawhub.ai, 700+ community skills). Skills extend capabilities with tools,
  instructions, and plugins. Plugin SDK for building new capabilities. Skills are
  the primary extensibility mechanism.
- **Daemon system:** `openclaw onboard --install-daemon` sets up always-on agent. Gateway
  runs as a background service (systemd/launchd). Personal claw on a laptop or shared
  multiplayer claw — configuration is the only difference.
- **Model plugins:** Models and agent harnesses (Claude, Codex, local/Ollama) are swappable
  plugins. No lock-in.

### Self-Healing (via openclaw-self-healing plugin)

**URL:** https://github.com/Ramsbaby/openclaw-self-healing (fetched 2026-11-18)
**Stars:** 40 | **Status:** Not actively maintained (last 2026-07-25, pre-OpenClaw 2.0)

Five-level recovery ladder (the ticket says 4-tier but the actual implementation is 5):

1. **Restart** — plain process restart (watchdog)
2. **Config repair** — fix corrupted config from known-good backup
3. **Dependency fix** — reinstall broken dependencies
4. **AI diagnosis** — LLM analyzes logs and proposes a fix
5. **Escalate** — alert human, full diagnostic bundle

Built as shell scripts wrapping a long-running service. Uses systemd on Linux, launchagent
on macOS. The key insight: a plain restart is enough until the crash cause is NOT restartable
(corrupted config, missing key, broken dependency), then you need graduated recovery.

### Limpet Extraction

| OpenClaw Feature | Limpet Mapping | Priority | Notes |
|---|---|---|---|
| Skills (SKILL.md + ClawHub) | RE tool skills (wq#4872 RE MCP connectors) | HIGH | SKILL.md format is directly adoptable for Limpet RE tools; ClawHub model informs a Limpet skill registry |
| Daemon (always-on gateway) | Limpet always-on agent services | HIGH | systemd user unit pattern is directly reusable; Gateway as local control plane fits Limpet's MCP meta-server |
| 29+ channels | Channel-agnostic agent interface | MEDIUM | Limpet needs fewer channels (CLI, MCP, maybe SSH/web) but the adapter pattern is the right abstraction |
| Self-healing ladder | Self-maintaining workstation | MEDIUM | Graduated recovery is exactly what a RE workstation needs; adapt from 5-tier to Limpet's service set |
| Trusted gateway / untrusted execution | Agent authority model | HIGH | Core security architecture directly applicable |

---

## 2. Hermes Agent (NousResearch/hermes-agent)

**URL:** https://github.com/NousResearch/hermes-agent (fetched 2026-11-18)
**Stars:** ~264K | **License:** MIT | **Language:** Python
**Builder:** Nous Research

### Architecture

- **Self-improving learning loop:** Creates skills from experience, improves them during use,
  nudges itself to persist knowledge, searches past conversations, builds a deepening user
  model across sessions. Compatible with agentskills.io open standard.
- **Hybrid sandbox model — 6 built-in terminal backends:**
  - Local (bare metal)
  - Docker (container isolation)
  - SSH (remote execution)
  - Modal (serverless GPU)
  - Daytona (serverless persistence — hibernates when idle, wakes on demand)
  - Vercel Sandbox
  - Plus plugin backends (e.g., Singularity/Apptainer)
- **Persistent per-bot state:** Agent-curated memory with periodic nudges. FTS5 session
  search with LLM summarization for cross-session recall. Honcho dialectic user modeling.
- **Multi-channel:** Telegram as primary remote interface (talk from phone while agent works
  on cloud VM). Not as many channels as OpenClaw but the decoupled-interface pattern is there.
- **Runs anywhere:** $5 VPS to GPU cluster to serverless. Not tied to a laptop.

### Limpet Extraction

| Hermes Feature | Limpet Mapping | Priority | Notes |
|---|---|---|---|
| Hybrid sandbox (6 backends) | Flexible execution environments | HIGH | Limpet needs local + SSH + container at minimum; the backend-adapter pattern is the right abstraction |
| Self-improving skills | RE tool skill auto-improvement | MEDIUM | A skill that learns from repeated RE workflows is high-value but complex to implement |
| Persistent state / memory | Agent memory across sessions | HIGH | FTS5 search + LLM summarization is a proven pattern for Limpet's agent memory |
| agentskills.io compatibility | Interoperable skill format | LOW | Useful for ecosystem but not a launch priority |
| Daytona/Modal serverless | Hibernate-on-idle for RE tasks | LOW | Interesting for cloud RE but Limpet is local-first |

---

## 3. OpenMausBot (milind-soni/OpenMausBot)

**URL:** https://github.com/milind-soni/OpenMausBot (fetched 2026-11-18)
**Stars:** ~25K | **License:** Apache 2.0 | **Language:** TypeScript
**Formerly:** OpenGrokBot

### Architecture

- **VM-per-bot isolation:** Each bot gets its own environment:
  - Cloud Linux desktop (remote)
  - Isolated Local VM (QEMU/KVM)
  - Host control (macOS/Ubuntu Xorg, with platform safety certification)
  VM isolation is stronger than Docker containers — full OS-level separation.
- **Chat-app UX:** Every bot appears as a contact in a sidebar. Each has its own personality,
  model, cloud computer, and connected apps. Electron desktop app (macOS, Windows, Ubuntu).
- **Local-first:** Harness server on 127.0.0.1 owns all agent processes. Transcripts, keys,
  events in `~/.openmausbot`.
- **500+ app integrations:** Via Composio. OAuth-based sign-in for apps.
- **Approval cards:** Actions surface as approval cards; user reviews before execution.

### Limpet Extraction

| OpenMausBot Feature | Limpet Mapping | Priority | Notes |
|---|---|---|---|
| VM-per-bot isolation (QEMU/KVM) | Strong agent isolation | HIGH | Limpet already builds on QEMU/KVM; VM-per-agent isolation is natural |
| Approval cards UX | Agent authority UI | MEDIUM | Visual approval pattern for RE tool actions |
| 500+ app integrations (Composio) | External tool integrations | LOW | Limpet is a RE workstation, not an office tool — but pattern is reusable |
| Electron cross-platform | Desktop app pattern | LOW | Limpet is Linux-only; unnecessary |

---

## 4. open-duet / Suparagent (Anil-matcha/open-duet)

**URL:** https://github.com/Anil-matcha/open-duet (fetched 2026-11-18)
**Stars:** ~6.8K | **License:** Apache 2.0 | **Language:** TypeScript/Python
**Note:** Repo is `open-duet` but the product name is Suparagent

### Architecture

- **Named agents with isolated computers:** Each agent has: name, role, engine, workspace,
  memory, approval level. Agents get their own isolated computer (Docker-based).
- **Deny-by-default action gateway:** Three approval modes:
  - "Ask first" — pause for approval before actions that modify state
  - "Auto-approve safe" — read-only actions auto-approved, writes need approval
  - "Full auto" — everything approved (trusted environments only)
  Routines NEVER approve actions by themselves — human in the loop always.
- **Multi-engine support:** 10 engine adapters behind one contract:
  - CLI engines: Claude, Codex
  - ACP engines: Gemini CLI, Google Antigravity, Cursor, OpenCode, any ACP agent
  - API engines: OpenAI-compatible (Ollama, LM Studio, vLLM), OpenRouter
- **Teams and delegation:** "Create by chatting" — Chief of Staff proposes specialists
  around a goal. Team leads delegate to specialists. Shared project folders.
- **Routines:** Scheduled, webhook, or manual runs. Each result reviewed; routine never
  self-approves.
- **Audit trail:** All actions logged with engine, approval status, timestamp.

### Limpet Extraction

| Suparagent Feature | Limpet Mapping | Priority | Notes |
|---|---|---|---|
| Deny-by-default gateway | Agent authority (wq#4859 SysKnife + wq#4864 Agent Passport) | HIGH | Core security pattern — Limpet agents must ask before acting on hardware |
| Named agents + roles | RE specialist agents (firmware, protocol, binary) | MEDIUM | Natural fit for Limpet's RE tool categories |
| Multi-engine adapters | Model-agnostic agent runtime | MEDIUM | Limpet already uses LiteLLM; the adapter-contract pattern reinforces this |
| Routines (scheduled work) | Automated RE workflows | MEDIUM | Scheduled firmware scans, periodic analysis runs |
| Audit trail | RE action provenance | HIGH | Every RE action on a device must be traceable |

---

## 5. ZeroClaw (zeroclaw-labs/zeroclaw)

**URL:** https://github.com/zeroclaw-labs/zeroclaw (fetched 2026-11-18)
**Stars:** ~33K | **License:** MIT OR Apache 2.0 | **Language:** Rust (edition 2024)

### Architecture

- **Single Rust binary:** Agent runtime compiled to one binary. Talks to 20+ LLM providers
  (Anthropic, OpenAI, Ollama, etc.). 30+ channels (Discord, Telegram, Matrix, email, voice,
  webhooks, CLI). Tools: shell, browser, HTTP, hardware, custom MCP servers.
- **Lightweight footprint:** Runs in under 5MB RAM (per zeroclaw.net, fetched 2026-11-18).
  Designed for constrained environments — VPS, Raspberry Pi, embedded systems.
- **Security model (defense in depth):**
  - Default autonomy: `supervised` (medium-risk needs approval, high-risk blocked)
  - Workspace boundaries and command policy
  - OS-level sandboxes: Landlock (Linux), Bubblewrap, Seatbelt (macOS), Docker
  - Cryptographic tool receipts on every action
  - YOLO mode for trusted dev environments
- **Gateway + dashboard:** HTTP/WebSocket gateway with web dashboard for chat, memory
  browsing, config editing, cron management, tool inspection.
- **ACP support:** Agent Client Protocol (JSON-RPC 2.0 over stdio) for IDE integration.
- **Firmware directory:** Has a `firmware/` directory in the repo — indicates embedded/
  hardware awareness in the design.

### Limpet Extraction

| ZeroClaw Feature | Limpet Mapping | Priority | Notes |
|---|---|---|---|
| Rust single binary | Limpet Rust kernel agent runtime (wq#4856 Octopus-SI-OS) | HIGH | Directly portable pattern; <5MB RAM fits constrained RE hardware |
| Landlock/Bubblewrap sandboxing | OS-level agent sandboxing | HIGH | Linux-native sandboxing is exactly what Limpet needs |
| Cryptographic tool receipts | RE action provenance | HIGH | Every tool invocation cryptographically signed — audit-grade |
| 30+ channels | Channel adapters | MEDIUM | Same pattern as OpenClaw but in Rust |
| Supervised autonomy levels | Agent authority tiers | HIGH | Maps to Limpet's graduated trust model |
| firmware/ directory | Hardware-aware agent | HIGH | Indicates the runtime already considers embedded targets |

---

## 6. TrustClaw / secure-openclaw (ComposioHQ)

**URL:** https://github.com/ComposioHQ/openclaw-composio (fetched 2026-11-18)
**Note:** The original ticket references `ComposioHQ/secure-openclaw` but Composio's actual
OpenClaw integration is `openclaw-composio`. The security features come from Composio's
platform, not a standalone repo.

### Architecture

- **Composio platform integration:** Gives OpenClaw (and other agents) secure access to
  1700+ tools via one MCP gateway. Auth handled per-tool via OAuth.
- **OWASP Agentic Security alignment:** 56 automated checks across 8 categories:
  - Exposed gateway ports
  - Weak permissions
  - Missing auth
  - Plaintext secrets
  - And 4 more categories (per awesome-openclaw-plugins-composio listing)
- **Firewall:** Blocks instructions hidden in tool results before the agent reads them
  (prompt injection defense).
- **Skill distillation:** Repeated successful tool-call sequences distilled into reusable
  skills automatically.
- **Triggers:** Background agents started from events across connected apps.
- **Recurring workflows:** Scheduled tasks with security constraints at every step.

### Limpet Extraction

| Composio Feature | Limpet Mapping | Priority | Notes |
|---|---|---|---|
| OWASP agent security checks | Limpet agent security baseline | HIGH | 56-check framework is a reusable audit template for Limpet agents |
| Prompt injection firewall | Tool result sanitization | HIGH | Critical for RE tools that read untrusted firmware/binary data |
| Skill distillation | Auto-generated RE workflows | MEDIUM | Learn from repeated RE tool sequences |
| Triggers (event-driven agents) | Hardware-event-driven RE | LOW | Interesting but not launch priority |

---

## Reference Lists Assessment

### awesome-grok-bot (ZeroPointRepo/awesome-grok-bot)

**URL:** https://github.com/ZeroPointRepo/awesome-grok-bot (fetched 2026-11-18)
**Entries:** 63 verified Grok Bot skills/plugins/MCP servers + alternatives

Curated, verified directory. Machine-readable formats (CATALOG.md, catalog.csv,
plugins.json, llms.txt). Tracks the Grok Bot ecosystem from day one (beta 2026-08-11).
Useful as a template for Limpet's own tool/skill registry format.

### awesome-openclaw (YarnSh39/awesome-openclaw)

**URL:** https://github.com/YarnSh39/awesome-openclaw (fetched 2026-11-18)

Comprehensive resource list. Key items for Limpet:
- ClawHub: 700+ community skills (pattern for Limpet skill registry)
- Security section: audit and hardening resources
- Installation guides for various platforms
- MCP support documentation

---

## Consolidated Limpet Architecture Mapping

### Tier 1 — HIGH priority, directly adoptable

| Feature | Best Source | Limpet Component | Related Tickets |
|---|---|---|---|
| Skills/plugin architecture (SKILL.md) | OpenClaw | RE tool skills | wq#4872 |
| Deny-by-default action gateway | Suparagent (open-duet) | Agent authority | wq#4859, wq#4864 |
| Rust single-binary agent runtime | ZeroClaw | Kernel agent runtime | wq#4856 |
| OS-level sandboxing (Landlock/Bubblewrap) | ZeroClaw | Agent isolation | — |
| Cryptographic tool receipts | ZeroClaw | RE action provenance | — |
| Hybrid sandbox backends | Hermes Agent | Execution environments | — |
| VM-per-agent isolation | OpenMausBot | Strong isolation | — |
| Trusted gateway / untrusted execution | OpenClaw | Security architecture | — |
| OWASP agent security checks | Composio | Security baseline | — |
| Prompt injection firewall | Composio | Tool result sanitization | — |
| Graduated self-healing | openclaw-self-healing | Self-maintaining workstation | — |
| Persistent agent memory (FTS5 + LLM) | Hermes Agent | Agent memory | — |

### Tier 2 — MEDIUM priority, adapt for Limpet context

| Feature | Best Source | Limpet Component |
|---|---|---|
| Channel-agnostic adapters | OpenClaw/ZeroClaw | CLI + MCP + SSH interface |
| Named agents with roles | Suparagent | RE specialist agents |
| Multi-engine model adapters | Suparagent | Model-agnostic runtime (via LiteLLM) |
| Scheduled routines | Suparagent | Automated RE workflows |
| Audit trail | Suparagent | Action provenance |
| Self-improving skills | Hermes Agent | RE skill refinement |
| Approval cards UX | OpenMausBot | Agent authority UI |
| Skill distillation | Composio | Auto-generated workflows |
| Supervised autonomy tiers | ZeroClaw | Graduated trust |

### Tier 3 — LOW priority, nice-to-have or future

| Feature | Best Source | Notes |
|---|---|---|
| 500+ app integrations | OpenMausBot/Composio | Office tools, not RE |
| Serverless hibernate | Hermes Agent | Cloud-first, Limpet is local |
| Cross-platform desktop | OpenMausBot | Limpet is Linux-only |
| agentskills.io | Hermes Agent | Ecosystem interop, not launch |
| Event-driven triggers | Composio | Hardware events later |

---

## Recommended Implementation Order for Limpet

1. **Agent authority gateway** (deny-by-default from Suparagent + supervised autonomy from
   ZeroClaw) — this is the security foundation. Connects to wq#4859 SysKnife and wq#4864
   Agent Passport.

2. **Skills architecture** (SKILL.md format from OpenClaw) — adopt for RE tool skills.
   Each RE tool gets a SKILL.md declaring capabilities, required permissions, input/output
   schemas. Connects to wq#4872 RE MCP connectors.

3. **Rust agent runtime** (ZeroClaw patterns) — if Limpet's kernel goes Rust per wq#4856
   Octopus-SI-OS evaluation, ZeroClaw's single-binary pattern is the reference
   implementation. <5MB RAM, Landlock sandboxing, tool receipts.

4. **Execution sandboxing** (VM from OpenMausBot + Landlock from ZeroClaw + hybrid backends
   from Hermes) — layered isolation: Landlock for lightweight tool calls, VM for untrusted
   firmware analysis, SSH for remote targets.

5. **Self-healing** (openclaw-self-healing pattern) — graduated recovery for Limpet's
   always-on agent services. Adapt the 5-tier ladder to Limpet's service set.

6. **Agent memory** (Hermes Agent FTS5 + LLM pattern) — persistent cross-session memory
   for RE workflows. Learn from past analyses.

7. **Security baseline** (Composio OWASP checks + prompt injection firewall) — adapt the
   56-check framework for Limpet's RE agent context. Tool result sanitization is critical
   when reading untrusted binary/firmware data.

---

## Key Architectural Insight

The Octopus-SI-OS primitives (wq#4856) + these extracted features = Limpet agent OS
architecture. The convergence is:

```
ZeroClaw Rust runtime
  + OpenClaw skills/channels/gateway
  + Suparagent deny-by-default authority
  + Hermes hybrid sandbox + learning loop
  + OpenMausBot VM isolation
  + Composio security framework
  = Limpet Agent OS
```

The common pattern across ALL six projects: **trusted local gateway controlling untrusted
agent execution, with graduated authority levels and cryptographic audit trails.** This is
exactly the security model Limpet needs for a RE workstation where agents interact with
potentially hostile firmware and hardware.
