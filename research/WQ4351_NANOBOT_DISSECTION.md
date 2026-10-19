# wq#4351 — LIMPET RESEARCH: dissect NanoBot (HKUDS)

**What NanoBot is:** a real, actively-maintained open-source project at
`github.com/HKUDS/nanobot`, out of a University of Hong Kong data-science lab
(HKUDS; the project's own interview coverage names the group's lead as
Prof. **Huang Chao** — "港大黄超"). Launched **2026-02-01** as *"an
ultra-lightweight personal AI assistant inspired by Clawdbot"* — Clawdbot
being OpenClaw's original pre-rename name, the same lineage NanoClaw
(wq#4348) targets. Pure **Python ≥3.11, single asyncio event loop**, MIT
licensed, `pip install nanobot` → `nanobot onboard`. Core agent loop is
reported at **~3,400–4,000 LOC** (the README itself says "run
`core_agent_lines.sh` to verify anytime," and different commit snapshots
show the number moving: 3,359, 3,422, ~4,000 — it's a live, self-checking
claim, not a frozen one) against Clawdbot/OpenClaw's reported **430k+ LOC —
roughly a 99% reduction**. A third-party structured wiki ingest puts the
**full repo** (channels, providers, CLI, tests included) at **~16k lines**,
so "4,000 LOC" specifically means the core agent loop, not the whole
project. Went from 0 to **32k–41k GitHub stars within its first month**
(sources disagree by snapshot date — one Chinese-press piece says "5,000+
stars and 700+ forks within three days," another says 32K within a month,
a later search hit says 41K) and spawned a visible ecosystem: a TypeScript
port (`@jcheesepkg/nanobot`, "zero native dependencies, runs in Node.js or
WebContainer"), Docker Hub mirror images, PyPI re-packagers, and a dedicated
line-by-line reading repo (`bcefghj/learn-nanobot`).

**Sourcing note (instinct 8 — anti-fabrication):** every claim below came
from `fleet-search web`/`github` on 2026-10-19 (SearXNG's general engines
were all suspended/CAPTCHA'd this run — brave, duckduckgo, google cse,
startpage all failed; the keyed DeepSeek-Search fallback carried the whole
search), not memory — NanoBot postdates the model training cutoff and did
not exist as a named project before 2026-02. Sources used: the official
`HKUDS/nanobot` GitHub repo (README snapshots at multiple commit hashes,
PR #554 "feat(tools): add mcp support," PR #6166, release-tag diffs on
`newreleases.io` from v0.1.3.post4 through v0.3.5), a Mintlify-hosted docs
mirror (`mintlify.wiki/HKUDS/nanobot`), a third-party structured wiki ingest
(`googs1025.github.io/llm-wiki/entities/nanobot.html`), an annotated
line-by-line reading repo (`bcefghj/learn-nanobot`), Chinese tech press
(two separate Tencent Cloud developer-community writeups, a Toutiao piece,
a BAAI hub syndication, a Jazzyear interview with the HKUDS lead), and
PyPI/npm package listings for third-party ports. Where only one source
carries a number (star count, LOC count) I've flagged the disagreement
rather than picking one silently.

---

## Architecture (single-process, async, layered — no containers, no queue broker)

```
  CHANNELS (BaseChannel subclasses)
  Telegram · Discord · Slack · Feishu · DingTalk · Mochat · IMAP/SMTP · web
        │  channel.send() / channel.receive()
        ▼
  MessageBus — in-process asyncio queues, one per conversation
        ▼
  AgentLoop._dispatch()
        ▼
  AgentRunner.run() ──▶ LLMProvider
        │                 11+ providers: OpenAI, Anthropic (+ prompt
        │                 caching), VolcEngine, OpenAI Codex (OAuth),
        │                 vllm (local), StepFun, + any OpenAI-compatible
        │                 endpoint (LM Studio, llama.cpp, Together AI,
        │                 Fireworks, Azure OpenAI) via a "custom provider"
        │                 passthrough that bypasses LiteLLM entirely
        ▼
  Tool execution layer
        ├─ built-in tools (exec, notebook-edit, web search incl. Kagi, …)
        ├─ MCP-wrapped tools — nanobot/agent/tools/mcp.py, official MCP
        │  Python SDK; MCP resources/prompts also exposed as read-only
        │  tools; multiple MCP servers connect in isolated tasks (not a
        │  shared race)
        ├─ Skills — SKILL.md files, dynamically loaded into the system
        │  prompt only when relevant; ClawHub marketplace to search/install
        │  public skills
        └─ SubAgents — SpawnTool creates background sub-agents with their
           own iteration budget (15 vs. the main agent's own separate cap)
        ▼
  Memory — plain Markdown files on disk, no vector DB, per-workspace
  isolation; "save_memory" is not a real tool call, it's handled directly
  inside the framework to cut I/O
```

## 1. Minimal dependency footprint, self-verifying

- The **core agent loop** — config, message bus, LLM call, tool-call loop —
  is the ~3,400–4,000 LOC the marketing number refers to. The README ships
  its own falsifiable check: a `core_agent_lines.sh` script Frank-style
  skeptics (or us) can run to get the real current number rather than
  trusting a stale blog claim. That number has visibly moved between
  commits (3,359 → 3,422 → ~4,000 across snapshots), consistent with normal
  development, not a one-time number frozen for marketing.
- The **full repository** (channels, 11+ provider adapters, CLI, tests,
  docs) is reported separately at **~16k lines** by a third-party
  structured-wiki ingest — the "4,000 LOC" claim is scoped to the
  reviewable agent core, not the whole tree, same caveat NanoClaw's dissect
  (wq#4348) flagged for its own "<4,000 LOC" number.
- **No C extensions**; one dependency surface (pure Python, asyncio). No
  per-agent container/VM boundary like NanoClaw — NanoBot's "lightweight"
  axis is LOC/dependency count, not process isolation. That's a genuinely
  different lever on the same "OpenClaw is too big" problem NanoClaw
  attacks via sandboxing instead.

## 2. MCP support, added as a small bolt-on, not a redesign

- Landed in **PR #554**, "feat(tools): add mcp support," merged into
  **v0.1.4** (changelog: *"MCP Support — Connect external tool servers via
  Model Context Protocol"*). The PR's own description: **"~120 lines of
  code across 5 files, using the official MCP Python SDK."**
- Mechanism: `nanobot/agent/tools/mcp.py` wraps each MCP server tool as a
  regular NanoBot `Tool` and registers it normally — *"the agent loop
  doesn't know or care that they come from MCP."* MCP resources and prompts
  are exposed as read-only tools too (later release note), so the agent can
  query them directly rather than needing bespoke resource-fetch code.
  Multiple MCP servers connect in **isolated tasks instead of racing each
  other** (a later bugfix release note) — a concrete concurrency fix, not
  a hypothetical.
- Config supports custom auth headers for MCP servers (2026-02-18 changelog
  entry), so authenticated MCP servers aren't a special case.
- **This is the single most load-bearing data point for the ticket's LIMPET
  framing**: a production-grade MCP client integration, covering wrapping,
  discovery, auth, and concurrency safety, fit in ~120 LOC on top of an
  already-generic `Tool` abstraction. MCP support here isn't a separate
  subsystem; it's a thin adapter because the tool-call interface it adapts
  *into* was already minimal and generic.

## 3. Multi-provider / multi-channel breadth despite the small core

- **11+ LLM providers** cited in press coverage: OpenAI, Anthropic (with
  prompt caching), VolcEngine, OpenAI Codex (OAuth-based, not API-key),
  vllm for local/self-hosted models, StepFun, plus a **generic
  OpenAI-compatible "custom provider"** passthrough (bypasses LiteLLM
  entirely, passes the model name as-is) that covers LM Studio, llama.cpp,
  Together AI, Fireworks, and Azure OpenAI without NanoBot needing
  provider-specific code for each.
- **10+ chat platforms**: Telegram (documented as the recommended/easiest
  setup), Discord, Slack, Feishu, DingTalk, Mochat, plus IMAP/SMTP email —
  each a `BaseChannel` subclass with its own credential setup flow
  documented individually (bot token for Telegram/Discord, App ID+Secret
  for Feishu/DingTalk, bot token + app-level token for Slack).
- Each channel/provider is an isolated adapter behind a common interface
  (`BaseChannel`, `LLMProvider`) — this is *why* 10+ channels and 11+
  providers didn't balloon the core LOC count: breadth lives in adapters,
  not in the core loop that the "4,000 lines" claim describes.

## 4. Memory: files, not infrastructure

- Long-term memory is **plain Markdown files on disk** — explicitly no
  vector database, no embedding service, no external dependency. The
  stated rationale (from the annotated `learn-nanobot` reading repo):
  **transparency** (a user can read/edit their own agent's memory file
  directly), **zero dependencies** (no vector DB to run or pay for), and
  **per-workspace isolation** (each project gets its own memory space).
- `save_memory` is **not a real tool call** — it's handled directly inside
  the framework rather than round-tripping through the LLM tool-call
  protocol, cutting unnecessary I/O. A deliberate "virtual tool" pattern:
  looks like a tool to the model, costs nothing like a real one to the
  runtime.
- A later release note ("memory summaries cumulative," PR #5610 on a fork)
  shows the memory system evolving incrementally (summarization on top of
  raw files) rather than being replaced by heavier infrastructure as the
  project matured.

## 5. Skills (SKILL.md) + ClawHub marketplace

- Skills are **SKILL.md files**: a description (when to activate) plus
  detailed instructions (how to execute), loaded **dynamically into the
  agent's system prompt only when relevant** — not all skills loaded
  all the time, which is the actual mechanism keeping the core prompt (and
  by extension the reviewable core) small as capability grows.
- **ClawHub** (2026-02-16 changelog: *"nanobot now integrates a ClawHub
  skill — search and install public agent skills"*) is a marketplace for
  third-party skills — the same "push new capability out of core, into an
  ecosystem layer" pattern NanoClaw enforces by contribution policy
  (wq#4348 §4), but implemented here as a discoverable skill registry
  rather than a "fork the repo" instruction.
- Users can write custom skills loaded on demand — same shape as
  Limpet's own skill-loading convention, independently arrived at.

## 6. SubAgents (SpawnTool)

- The main agent can spawn **background sub-agents** via a `SpawnTool`.
  Sub-agents get their **own iteration budget, separate from and larger
  than the main agent's** (one source cites 15 iterations for sub-agents
  vs. 4 for the dispatching main agent — i.e. the main loop stays cheap
  and interactive while delegated work gets more runway). Subagents work
  in CLI mode as of the 2026-02-19 release, not just inside channel
  sessions.

## 7. Deploy story: "2-minute deploy," Docker support added early

- Chinese press framing (Toutiao, independently of the GitHub repo):
  **"2-minute deploy, clear architecture, easy to learn core logic"** as
  the explicit value proposition alongside the LOC reduction — deploy speed
  is treated as a first-class selling point, not incidental.
- `pip install nanobot` → `nanobot onboard` is the baseline path. **Docker
  support shipped early** (v0.1.3.post4 changelog: *"multi-provider &
  Docker support"*, 2026-02-04, three days after the 2026-02-01 launch) —
  not a one-click PaaS button (no Railway/Fly.io template surfaced in
  search), but a packaged container path landed fast relative to launch.
- The "research-ready" framing isn't marketing fluff layered on top — it's
  a literal bullet in the project's own README's "Key Features" list
  (*"🔬 Research-Ready: Clean, readable code that's easy to understand,
  modify, and extend for research"*), sitting right next to the
  ultra-lightweight bullet. The ticket's framing (3) — "research-workflow
  focus" — traces directly to this stated design goal, not an inference.

---

## Feature-extraction list (what NanoBot actually ships)

1. **~3,400–4,000 LOC core agent loop**, self-verifying via a shipped
   `core_agent_lines.sh` script; ~16k LOC for the full repo including
   channel/provider adapters.
2. **MCP client support in ~120 LOC / 5 files** (PR #554): generic `Tool`
   wrapper for any MCP server tool, resources/prompts exposed as read-only
   tools, isolated-task connection per server, custom auth headers.
3. **11+ LLM providers** behind one `LLMProvider` interface, including a
   generic OpenAI-compatible passthrough that covers endpoints NanoBot
   never wrote bespoke code for.
4. **10+ chat/email channels** behind one `BaseChannel` interface.
5. **File-based memory** (Markdown, no vector DB), with `save_memory`
   implemented as a zero-I/O virtual tool rather than a real tool-call
   round-trip.
6. **Dynamically-loaded skills** (`SKILL.md`) plus a public marketplace
   (ClawHub) for third-party skills — capability growth routed out of the
   reviewable core.
7. **SubAgents via SpawnTool** with an independent, larger iteration
   budget than the dispatching main agent.
8. **Docker packaging shipped 3 days post-launch**; pip-installable CLI
   onboarding flow as the primary path.
9. **"Research-Ready" is a stated, shipped design goal** (README Key
   Features), not just the ticket's own framing.

## Recommendations for Limpet

Limpet already runs an **MCP meta-server** (`limpet/tools/rockchip/
rockchip-mcp.toml`, wq#3808/wq#3935, `limpet/DESIGN-DECISIONS.md:201-227`)
for device-profile tool dispatch. NanoBot is useful here as a **minimum-
viable-MCP-integration benchmark**, not an architecture to copy wholesale —
it proves how little code a correct MCP client needs when the tool
abstraction underneath it is already generic.

- **A. Use NanoBot's ~120-LOC MCP client as the complexity floor.** If
  Limpet's own MCP wrapper/dispatch code for a given tool family (Rockchip
  or otherwise) is an order of magnitude larger than that for equivalent
  functionality (discovery, auth, isolated-task connection, resource
  exposure), that's a signal the generic tool abstraction underneath isn't
  generic enough yet — the fix is usually in the abstraction, not in
  adding more wrapper code.
- **B. Adopt the "virtual tool" pattern for cheap, framework-internal
  operations.** NanoBot's `save_memory` looks like a tool to the model but
  is handled directly in-process, skipping a real tool-call round-trip.
  Any Limpet operation that's logically a tool call but cheap and
  framework-owned (e.g., writing to `evidence.json`, appending to
  `ISSUE-LOG.md`) is a candidate for the same treatment — exposed to the
  model as a tool for discoverability, but short-circuited internally.
- **C. Markdown-file memory, not infrastructure, for anything that fits.**
  NanoBot's bet — memory as plain, user-editable Markdown files instead of
  a vector DB — buys transparency and zero extra infra at the cost of
  semantic search. Limpet's own memory/evidence trail is already
  file-based (`evidence.json`, `ISSUE-LOG.md` rows); this is a validation
  of that choice, not a new recommendation, but it's worth stating
  explicitly as the reason not to reach for a vector store the next time
  someone proposes "smarter" agent memory for Limpet.
- **D. Route new capability through a skill-loading layer, not core
  growth.** NanoBot's SKILL.md + ClawHub pattern (dynamic, on-demand system
  prompt inclusion plus a discoverable registry) is the same shape Limpet
  already uses for skills; the useful data point is that NanoBot treats
  *marketplace discoverability* as part of the pattern, not just dynamic
  loading — worth considering if Limpet skills grow past what a single
  operator curates by hand.
- **E. Feature-to-code ratio, concretely.** NanoBot ships 11+ providers,
  10+ channels, MCP, skills, subagents, and file-based memory in ~4,000
  core LOC (~16k full repo) by pushing breadth into thin, uniform adapters
  behind two interfaces (`LLMProvider`, `BaseChannel`) and keeping the
  loop itself minimal. That's the literal benchmark the ticket asked for:
  if a Limpet feature can't be expressed as a thin adapter behind an
  existing interface, and instead needs its own new abstraction, that's
  the signal it's surface area rather than core — the same test NanoBot's
  own maintainers appear to apply before anything lands in their reviewed
  ~4,000-line core.
