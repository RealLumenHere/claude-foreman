# wq#4351 — LIMPET RESEARCH: dissect NanoBot (HKUDS)

**What NanoBot is:** a real, actively-maintained open-source project at
`github.com/HKUDS/nanobot`, out of a University of Hong Kong data-science lab
(HKUDS; the project's own interview coverage names the group's lead as
Prof. **Huang Chao** — "港大黄超"). Launched **2026-02-01** as *"an
ultra-lightweight personal AI assistant inspired by Clawdbot"* — Clawdbot
being OpenClaw's original pre-rename name, the same lineage NanoClaw
(wq#4348) targets. Pure **Python ≥3.11, single asyncio event loop**, MIT
licensed, `pip install nanobot-ai` → `nanobot onboard` (the PyPI
*distribution* is `nanobot-ai`; the *console script* it installs is `nanobot` —
collapsing the two is how the original version of this line got it wrong, see
**Errata E1**). Core agent loop is
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

## Errata (wq#4386 verify-pass → wq#4401 judgment, 2026-10-19)

This doc shipped on secondary sources for several claims because the primary
search path was down when it was written (see the sourcing note above: all of
SearXNG's general engines were suspended/CAPTCHA'd that run). A verify-pass
(wq#4386) found one primary-source contradiction; the wq#4401 judgment pass
then re-audited **every precise identifier and number the doc stated without a
primary-source link**, because that class of claim is what a press/wiki
paraphrase degrades. Three corrections resulted, all applied inline above.
Historical claims are cited against commit **`a1241ee6`** (2026-03-14) rather
than `main`, so they stay re-checkable.

| # | Claim as shipped | Primary source | Status |
|---|---|---|---|
| E1 | `pip install nanobot` | `pyproject.toml:2` → `name = "nanobot-ai"`; `pypi.org/pypi/nanobot/json` is an unrelated Jetson robot-navigation package by "NanoBot Team" | **Wrong — fixed.** Installed the wrong software. `pyproject.toml:73` at `a1241ee6` (`nanobot = "nanobot.cli.commands:app"`, under `[project.scripts]` at `:72`) explains the slip: the console script *is* `nanobot`, the distribution is not. On `main`@`909b277e` the same entry is `:117` → `nanobot = "nanobot.cli.entry:main"` (see **Errata E4**) |
| E2 | subagents get 15 iterations "vs. 4" for the main agent, so delegated work has "more runway" | `nanobot/agent/subagent.py:115` → `max_iterations = 15`; `nanobot/agent/loop.py:56` → `max_iterations: int = 40` | **Wrong — fixed.** Second number was 40, not 4, so the stated rationale was inverted |
| E3 | `save_memory` is "not a real tool call", a "zero-I/O virtual tool" | `nanobot/agent/memory.py:21-45` (`_SAVE_MEMORY_TOOL` function schema), `:114` (`consolidate()`), `:139` (forced `tool_choice`) | **Wrong — fixed**, and Recommendation B rewritten: it is a real forced tool call costing an extra round-trip, on a separate consolidation agent |

Checked and **upheld** in the same pass, for the record: `SpawnTool` is real
(`nanobot/agent/tools/spawn.py:11`, since renamed `SubagentTool` — wq#4386
flagged this unresolved only because it looked at current `main`, where the
old path is gone); the LiteLLM-bypass framing was accurate as of the doc's
sources (now superseded, §3); `@jcheesepkg/nanobot` on npm and
`bcefghj/learn-nanobot` on GitHub both exist under exactly those names;
ClawHub is real (`nanobot/skills/clawhub/SKILL.md`); Markdown-file memory with
no vector DB is real; "10+ channels" is conservative (19 channel dirs under
`nanobot/channels/` on `main`@`909b277e`). wq#4386 separately re-confirmed PR
#554's content, the `LLMProvider`/`BaseChannel` names, and
`core_agent_lines.sh`.

**The transferable lesson** is in `kb/ISSUE-LOG.md` row 1474: one
primary-source contradiction in a doc whose sourcing note admits the primary
path was unavailable is a *sampling signal*, not an isolated typo — audit the
claims sharing that provenance, not just the one that failed.

### Errata, second round (wq#4404 verify-pass → wq#4406 judgment, 2026-10-19)

wq#4404 re-checked the citations the wq#4401 pass above had *added* and
reported two as fabricated. Both are real errors and are fixed below, but
neither was a fabrication — each quoted string exists verbatim in the repo,
and the wq#4404 characterisation overshot in both cases. wq#4406 then closed
the population: all fifteen precise identifiers the wq#4401 diff introduced
are now re-checked against SHA-pinned primary sources (`a1241ee6` for
historical claims, `909b277e` for `main`), so there is no unaudited remainder
left to dispatch a further pass against.

| # | Claim as shipped by wq#4401 | Primary source | Status |
|---|---|---|---|
| E4 | E1's mechanism citation: `pyproject.toml:117` is `nanobot = "nanobot.cli.entry:main"`, offered as evidence at pinned `a1241ee6` | At `a1241ee6` the file is 117 lines, `:117` is `testpaths = ["tests"]`, and `entry` occurs nowhere in it (`grep -c entry` → 0); the console script is `:73` → `nanobot = "nanobot.cli.commands:app"`. The quoted line/number pair is exactly right for `main`@`909b277e`, where `[project.scripts]` sits at `:116` and `:117` is `nanobot = "nanobot.cli.entry:main"` | **Mis-attributed, not fabricated — fixed.** Right text, right line, wrong ref: a current-`main` reading printed under the pinned-commit citation. Conclusion (distribution ≠ console script) unaffected |
| E5 | `main` replaced LiteLLM with native adapters "`anthropic`, `azure_openai`, `bedrock`, `github_copilot`, `openai_codex`, `openai_compat`, `xai_grok`, `dashscope`" | `nanobot/providers/registry.py` on `main`@`909b277e`: `backend` takes exactly seven values (`:71-72`), one `*_provider.py` each; `dashscope` is a `ProviderSpec` (`:693-702`) whose `backend="openai_compat"`, one of 39 such specs out of 46 | **Wrong category, not a fabricated name — fixed.** DashScope is a configured provider, not a native adapter; the adapter list is seven, not eight. wq#4404's "zero hits anywhere in the repo for dashscope/qwen" is itself incorrect: it searched the recursive git tree, which lists *paths* only, so a provider declared inside `registry.py` was invisible to it (`qwen` is at `:695`) |
| E6 | "18 channel dirs on current `main`" | 19 directories under `nanobot/channels/` on `main`@`909b277e` (dingtalk, discord, email, feishu, linear, matrix, mattermost, mochat, msteams, napcat, qq, signal, slack, telegram, websocket, wecom, weixin, whatsapp, whatsapp_agent) | **Off by one — fixed.** "10+ channels is conservative" holds either way |

Re-checked clean in this round, independently rather than trusted: E2
(`subagent.py:115` = `max_iterations = 15`; `loop.py:56` =
`max_iterations: int = 40,`), E3 (`memory.py:21` = `_SAVE_MEMORY_TOOL = [`,
`:45` = `]`, `:114` = `async def consolidate(`, `:139` = the `forced` dict,
plus `chat_with_retry(…, tool_choice=forced)` at `:140-144` and the
`tool_choice="auto"` retry at `:151-155`, inside `class MemoryStore` at
`:75`), `spawn.py:11` = `class SpawnTool(Tool):` and its rename to
`class SubagentTool(Tool):` at `nanobot/agent/tools/subagent.py:59` on
`main`@`909b277e`, `pyproject.toml:2` = `name = "nanobot-ai"`, `:21` =
`"litellm>=1.82.1,<2.0.0",`, PyPI `nanobot` = "Minimalist robot navigation
framework" by "NanoBot Team", and `nanobot/skills/clawhub/SKILL.md` present
on `main`.

**The second transferable lesson** (`kb/ISSUE-LOG.md` row 1475): a
verify-pass that reports "fabricated" owes the same primary-source standard
it is enforcing. Both E4 and E5 were real defects, but calling them invented
mis-states the fix — one is a ref-mismatch across two refs the author was
legitimately reading, the other a category error about a name that does
exist — and E5's supporting evidence came from a git-tree listing searched as
if it were file contents.

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
           own iteration budget (15, vs. the main loop's default 40)
        ▼
  Memory — plain Markdown files on disk, no vector DB, per-workspace
  isolation; "save_memory" is a real forced tool call, but issued by a
  separate consolidation pass, not from the main agent loop
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
  - **Confirmed as of this doc's sources, since superseded.** At `a1241ee6`
    (2026-03-14) `litellm>=1.82.1,<2.0.0` was a hard dependency
    (`pyproject.toml:21`) and the only provider *implementations* under
    `nanobot/providers/` were `litellm_provider.py`, `custom_provider.py`,
    `azure_openai_provider.py` and `openai_codex_provider.py` (alongside
    `base.py`, `registry.py`, `transcription.py`, `__init__.py` — infra, not
    adapters) — i.e. the breadth (VolcEngine, StepFun, vllm, …) came
    *through* LiteLLM and the custom passthrough was indeed the bypass
    around it. On `main`@`909b277e` LiteLLM is **gone entirely** (zero
    case-insensitive occurrences in `pyproject.toml`), replaced by exactly
    **seven** native backend adapters — `openai_compat`, `anthropic`,
    `azure_openai`, `openai_codex`, `xai_grok`, `github_copilot`, `bedrock`
    (`nanobot/providers/registry.py:71-72`, one `*_provider.py` module
    each) — over which **46** declarative `ProviderSpec` entries are routed
    (`registry.py:215+`). Named vendors are specs, not adapters: DashScope
    (Qwen), for instance, is `ProviderSpec(name="dashscope", …,
    backend="openai_compat", default_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1")`
    at `registry.py:693-702` — 39 of the 46 specs ride `openai_compat` that
    way (see **Errata E5**). Read the "bypasses LiteLLM" framing as
    historical, not current.
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
- `save_memory` is **a real tool call, but not one the main agent loop ever
  sees** — it belongs to a separate memory-consolidation pass. Primary
  source (`nanobot/agent/memory.py` at `a1241ee6`): `_SAVE_MEMORY_TOOL`
  (lines 21-45) is a full function-tool schema, and
  `MemoryStore.consolidate()` (line 114) passes it to
  `provider.chat_with_retry(..., tools=_SAVE_MEMORY_TOOL,
  tool_choice=forced)` with `forced = {"type": "function", "function":
  {"name": "save_memory"}}` (line 139), falling back to `tool_choice="auto"`
  on providers that reject forced choice. So it costs an *extra* dedicated
  LLM round-trip rather than zero I/O. What's actually true is the narrower
  claim: consolidation is a separate agent with a one-tool toolbelt, which
  keeps `save_memory` out of the main loop's tool list and off the
  interactive path (see **Errata E3**).
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

- The main agent can spawn **background sub-agents** via a `SpawnTool` —
  primary-source confirmed as `class SpawnTool(Tool)` at
  `nanobot/agent/tools/spawn.py:11`, pinned at commit `a1241ee6`
  (2026-03-14). Since renamed: current `main` calls it `SubagentTool` at
  `nanobot/agent/tools/subagent.py`.
- Sub-agents get their **own iteration budget, smaller than and capped
  independently of the main agent's**: `max_iterations = 15` for a subagent
  (`nanobot/agent/subagent.py:115`) against the main loop's default
  `max_iterations: int = 40` (`nanobot/agent/loop.py:56`), both at
  `a1241ee6`. The "15 vs. 4" framing the secondary sources carried is wrong
  on the second number and therefore backwards on the rationale — delegated
  work gets *less* runway than the dispatching loop, not more, which reads
  as bounding the cost of fan-out rather than extending it (see
  **Errata E2**).
- Subagents work in CLI mode as of the 2026-02-19 release, not just inside
  channel sessions.

## 7. Deploy story: "2-minute deploy," Docker support added early

- Chinese press framing (Toutiao, independently of the GitHub repo):
  **"2-minute deploy, clear architecture, easy to learn core logic"** as
  the explicit value proposition alongside the LOC reduction — deploy speed
  is treated as a first-class selling point, not incidental.
- `pip install nanobot-ai` → `nanobot onboard` is the baseline path. **Docker
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
   implemented as a forced single-tool call on a *separate* consolidation
   agent, keeping it off the main loop's tool list.
6. **Dynamically-loaded skills** (`SKILL.md`) plus a public marketplace
   (ClawHub) for third-party skills — capability growth routed out of the
   reviewable core.
7. **SubAgents via SpawnTool** with an independent, *smaller* iteration
   budget than the dispatching main agent (15 vs. 40).
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
- **B. Keep framework-owned bookkeeping off the main loop's toolbelt by
  giving it its own single-tool agent.** *(Rewritten per Errata E3 — the
  original version of this recommendation was built on the claim that
  `save_memory` is a zero-I/O virtual tool, which the primary source
  contradicts.)* What NanoBot actually does is narrower and still useful:
  memory consolidation runs as a **separate pass with a one-tool toolbelt
  and a forced `tool_choice`**, so the main agent's tool list never carries
  `save_memory` and the interactive loop never spends an iteration on it.
  The transferable lever is **toolbelt scoping plus forced choice**, not
  short-circuiting: for a Limpet operation that is logically a tool call but
  framework-owned (writing `evidence.json`, appending an `ISSUE-LOG.md`
  row), run it as its own small forced-tool call rather than adding it to
  the main agent's tool list, where it competes for attention and iteration
  budget on every turn. Note the real cost honestly: this *buys* a clean
  main-loop toolbelt and *pays* an extra LLM round-trip — the opposite of
  the free-lunch framing. If zero-I/O is what Limpet wants, that's a
  genuinely different design (deterministic post-turn hook, no model in the
  loop) and NanoBot is not evidence for it.
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
