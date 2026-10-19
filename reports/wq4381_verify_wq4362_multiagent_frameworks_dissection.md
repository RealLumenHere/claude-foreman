# wq#4381 — verify wq#4362 (PR#2263, LIMPET RESEARCH: AutoGen/ADK/Semantic Kernel/CrewAI)

**Verdict: CONFIRM.** Every falsifiable claim, quote, and internal citation in
`research/WQ4362_MULTIAGENT_FRAMEWORKS_DISSECTION.md` holds under a fresh adversarial pass
against live, re-fetched primary sources. No fabricated quote, stat, API name, or citation
found.

## What I checked, independent of the author's report

Fetched PR#2263's head directly (`git fetch origin pull/2263/head` from the
`/home/lumen/worktrees/wq4381-a1` worktree, commit `96169a953b`, one file:
`research/WQ4362_MULTIAGENT_FRAMEWORKS_DISSECTION.md`, 287 lines added) rather than
trusting the ticket's own notes. Confirmed the squash-merge landed on `origin/main` as
`5d0354c8` is byte-identical to the PR head for that file (`git diff 96169a953b 5d0354c8 --
<file>` produced no output) — the content under review here is the content that actually
shipped.

### 1. AutoGen (§1) — actor-model API and the AG2 fork

- **Legacy `ConversableAgent.human_input_mode` behavior** — fetched
  `raw.githubusercontent.com/microsoft/autogen/0.2/autogen/agentchat/conversable_agent.py`
  directly. Confirmed verbatim: `ALWAYS` prompts every turn (line 1821), `TERMINATE` only
  prompts when `_is_termination_msg()` fires (lines 1830-1848), `NEVER` auto-replies
  `"exit"` past the consecutive-reply cap (line 1827, `reply = reply if reply or not
  self._is_termination_msg(message) else "exit"`). Matches the report's description
  exactly.
- **v0.4 actor-core API surface** — fetched
  `microsoft.github.io/autogen/0.4.8/reference/python/autogen_core.html` directly (507KB).
  `InterventionHandler`, `DropMessage`, `on_send`, `on_publish`, `SingleThreadedAgentRuntime`
  all present. `send_message`/`publish_message` confirmed on the companion
  message-and-communication guide page. The report's "actors with mailboxes" framing and the
  intervention-handler claim are accurate to the real API, not invented.
- **AG2 fork + 0.10.0 A2A announcement** — fetched the actual Google Developer Forums post
  (`discuss.google.dev/t/introducing-native-a2a-protocol-support-in-ag2-.../286168`, Nov 12
  2025, by Qingyun Wu and Vasiliy Radostev of the AG2AI Team). Every clause the report
  attributes to this post is an exact quote: "developers can now create multi-agent systems
  that work seamlessly with agents from LangChain, CrewAI, and any other A2A-compatible
  framework," and separately "Expose existing AG2 agents as A2A-compatible services that can
  be discovered and used by any A2A client" / "Connect to remote A2A agents built with any
  framework and use them as if they were native AG2 agents" — the report's bracketed
  paraphrase of these two bullets is faithful. The fork/governance framing ("AG2AI
  organization with open governance," PyPI packages `ag2`/`autogen`/`pyautogen`) is the
  post's own editor's note, verbatim.

### 2. Google ADK (§2) — `RemoteA2aAgent`, the circuit breaker, and its one documented gap

- **`RemoteA2aAgent` doc comment** — fetched
  `raw.githubusercontent.com/zeroasterisk/adk-elixir/main/lib/adk/agent/remote_a2a_agent.ex`
  directly (confirmed the repo and file exist via the GitHub trees API first, not guessed).
  The `@moduledoc` is an exact match to the report's quote: *"Agent that communicates with a
  remote A2A agent via A2A client. This agent wraps a remote A2A endpoint and forwards local
  events to it. It maintains a session with the remote agent using the `contextId` field."*
- **Benchmark quote from `code-with-rashid.github.io/agentic-arena`** — this is the single
  riskiest citation in the report (an obscure, specifically-named third-party GitHub Pages
  site — exactly the shape of citation a model could invent). Verified the site returns HTTP
  200, is a real multi-page benchmark lab (not a stub), located the actual sub-page
  (`/frameworks/google-adk/`) via search rather than guessing, and found the quote verbatim:
  *"RunConfig(max_llm_calls=N) is a real loop cap. Measured against a mock that never stops
  asking for tools, a budget of N produces exactly N requests on the wire — no off-by-one,
  no uncapped default. Compare Pydantic AI (retries is not a loop cap: 50 calls on a budget
  of 6), Agent Framework (uncapped by default: 41), and smolagents (one call beyond
  max_steps). ADK needed no correction at all."* Also confirmed `LlmCallsLimitExceededError`
  is named on the same page as the exception ADK raises instead of returning a blank answer.
- **`run_live()` gap, GitHub issue #5494** — fetched `api.github.com/repos/google/adk-
  python/issues/5494` directly. Real issue, title: *"[Live] run_live() silently terminates on
  max_llm_calls exhaustion — no event, no exception, no resumption path"* — matches the
  report's framing exactly. Issue body confirms every specific detail the report cites: no
  event emitted, no exception raised, session resumption doesn't apply, and "the only
  workaround we've found is to detect that `run_live()` has ended ... have the frontend
  create an entirely new session" — matches the report's "the only documented workaround is
  the caller detecting the dead generator and starting a whole new session" almost word for
  word.

### 3. Semantic Kernel (§3) — OpenAPI plugins and multi-language parity

- **`openapi_manager.py`'s `create_functions_from_openapi`** — fetched
  `raw.githubusercontent.com/microsoft/semantic-kernel/main/python/semantic_kernel/connectors/openapi_plugin/openapi_manager.py`
  directly; the function is defined at line 32. Confirmed.
- **Issue #12373, hardcoded 5-second timeout** — fetched
  `api.github.com/repos/microsoft/semantic-kernel/issues/12373` directly. Title: *"Python:
  Bug: add_plugin_from_openapi() has hardcoded 5 seconds timeout."* Body shows the exact
  workaround the report cites: constructing a custom `httpx.AsyncClient(timeout=...)` and
  passing it via `OpenAPIFunctionExecutionParameters(http_client=custom_client)`. One minor,
  immaterial wrinkle: the issue's own linked source line is in `openapi_runner.py`, not
  `openapi_manager.py` (the report cites `openapi_manager.py` as a general source for §3, not
  specifically for the timeout bug) — not a misattribution, just worth flagging for
  precision.
- **Discussion #10352, maintainer reply on OpenAPI version support** — fetched
  `api.github.com/repos/microsoft/semantic-kernel/discussions/10352` plus its `/comments`
  endpoint directly. The discussion's own title/body ("Semantic Kernel plugin with API
  manifest file") does *not* contain the version-support claim — but a maintainer
  (`moonbox3`) comment three replies in says, verbatim: *"Python supports the v3+ OpenApi
  spec and I believe that C# supports 2.0+ and 3.0+."* This is an exact match to the report's
  claim, correctly attributed as "an SK maintainer's reply," not the discussion's opening
  post. Initially flagged this as a possible mismatch until I pulled the comment thread —
  it checks out.
- **DeepWiki version-triplet (1.16.2/1.3.0/1.2.0)** — found via search (not DeepWiki itself,
  which the live fetch didn't need to touch): `microsoft/SemanticKernelCookBook`'s own GitHub
  README states *"The content of this book is based on Semantic Kernel dotnet-1.16.2 and
  python-1.3.0 and java-1.2.0."* Matches the report's "1.16.2 / 1.3.0 / 1.2.0 respectively"
  exactly.

### 4. CrewAI (§4) — delegation tools, Crews vs. Flows, memory

- **Delegation tool signatures** — fetched `docs.crewai.com/concepts/collaboration` directly
  (702KB). `grep` found `Delegate work to coworker(task: str, context: str, coworker: str)`
  and `Ask question to coworker(question: str, context: str, coworker: str)` verbatim,
  multiple times (prose and code block).
  Matches the report's "DelegateWork/AskQuestion primitives" claim exactly, including
  parameter names.
- **PR #4420, unified memory system** — fetched `api.github.com/repos/crewAIInc/crewAI/pulls/4420`
  directly. Title: *"New Unified Memory System."* Body: *"Replaces the four separate memory
  types (`ShortTermMemory`, `LongTermMemory`, `EntityMemory`, `ExternalMemory`) with a single
  `Memory` class where both encoding and retrieval are LLM-driven. Results are ranked by a
  composite score blending semantic similarity, recency, and importance -- not just vector
  distance."* This English PR description already corroborates the composite-score claim the
  report attributes to the KO-locale docs; I did not separately fetch the Korean locale page,
  but the underlying fact is independently confirmed from the PR itself.
- **`callsphere.ai` "Flows host Crews" quote** — fetched
  `callsphere.ai/blog/vw7g-crewai-flow-vs-crew-multi-agent-pattern-2026` directly. Exact
  match: *"In 2026 production, Flows host Crews — the Flow is the deterministic spine, the
  Crews are the LLM-heavy nodes."*

### 5. The MAF reframe (§5) — both secondary sources checked live

- **`visualstudiomagazine.com`** — the report names the domain without a URL; found the
  actual article by search
  (`visualstudiomagazine.com/articles/2026/04/06/microsoft-ships-production-ready-agent-
  framework-1-0-for-net-and-python.aspx`) and fetched it directly. Exact quote match: *"In
  practical terms, Microsoft Agent Framework is the company's attempt to provide one
  developer stack for building, orchestrating, and deploying AI agents and multi-agent
  applications. It combines work that previously lived in separate Microsoft efforts,
  especially Semantic Kernel and AutoGen, into a single open-source framework"* — and the
  article confirms the April 3 announcement date the report cites.
- **`atlan.com`** — found and fetched `atlan.com/know/ai-agent/microsoft/semantic-kernel/`
  directly. Exact quote match: *"Microsoft continues to address critical bugs and security
  issues in Semantic Kernel, but the majority of new features are built for Agent
  Framework... Fragmentation with AutoGen: Developers were confused about which framework to
  use, features overlapped, and the community was split. The launch of Agent Framework
  resolved this directly."* Also confirms the named orchestration patterns (sequential,
  concurrent, group chat, handoff, magentic) the report attributes to MAF.

### 6. Internal repo citations

- `design/wq3999_rex_skill_dissection.md` §4's exact phrase "may **not** see another's
  findings (`re-safety.md:28`)" is present at line 147 — the markdown bold markup around
  "not" is why a plain-text grep for the unbroken phrase initially missed it; reading the
  section directly resolved it.
- `limpet/model_router.py:47` is `def resolve_model(capability: str, *, capability_models:
  dict, resident_model: str) -> str:` — exact line match for the report's citation.
- All five referenced files (`design/wq3806_limpet_architecture.md`, `limpet/critic.py`,
  `limpet/obligations.py`, `research/WQ4356_A2A_PROTOCOL_DISSECTION.md`,
  `research/WQ4361_AGENT_RUNTIME_PATTERNS_DISSECTION.md`,
  `research/WQ4367_ORCHESTRATION_DISSECTION.md`) exist in the repo as claimed.

## Things I tried to break and couldn't

- Targeted the three most "inventable" citations in the report — an obscure named benchmark
  blog (`agentic-arena`), an obscure named production blog (`callsphere.ai`), and a
  third-party language port's doc comment (`adk-elixir`) — on the theory that a model
  fabricating sourcing would pick exactly this shape of citation (specific enough to sound
  researched, obscure enough to be hard to check). All three resolved to real, live pages
  with exact verbatim matches.
- Tried the Semantic Kernel discussion #10352 as a probable fabrication once the discussion's
  own title/body didn't contain the claimed quote — pulling the comment thread showed the
  claim is accurately attributed to a maintainer's reply further down, not the top-level
  post. A legitimate near-miss, not a defect.
- Tried the §6 recommendation for internal inconsistency against the sourced sections (e.g.,
  does "borrow AutoGen's actor-model shape" actually follow from §1's facts, does "do not
  adopt Semantic Kernel" follow from §3/§5) — each of the five numbered recommendations
  traces cleanly to a specific, verified finding from the sections above; no recommendation
  outruns its evidence.

## Not independently re-verified

- The CrewAI KO/PT-BR locale pages specifically (the English PR #4420 description already
  corroborates the underlying composite-score and per-agent-scope claims, so the locale
  detail is not load-bearing for the verdict).
- `google/adk-python`'s `invocation_context.py` source itself (the `increment_llm_call_count`/
  `LlmCallsLimitExceededError` naming is corroborated by both the agentic-arena benchmark and
  issue #5494's lived description, which was enough independent corroboration without opening
  the source tree).
- The §6 recommendation to build in-house rather than adopt a framework is a judgment call,
  not a factual claim — nothing to falsify there beyond the premises already checked above.

## Conclusion

CONFIRM. wq#4362's dissection is accurately sourced against all four frameworks and both
secondary MAF sources, including exact verbatim quotes, specific numbers, and API names
across official docs, GitHub issues/discussions/PRs, and three independently-checked
obscure third-party sources that would have been the easiest place to fabricate. The one
near-miss (discussion #10352) resolved in the report's favor on closer reading. No
escalation to opus-5@high warranted.
