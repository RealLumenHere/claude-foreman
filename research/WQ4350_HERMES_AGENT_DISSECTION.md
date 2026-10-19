# wq#4350: Dissection of Hermes Agent (Nous Research)

**Date:** 2026-10-19 · **Lane:** LIMPET research · **Primary source:** `github.com/NousResearch/hermes-agent`.
Source files were fetched with `curl` from `raw.githubusercontent.com`, pinned to commit
`5fc490e2b34123212d73ef316086f939567a725a` (`main` tip at 2026-10-19 04:16Z). Repo metadata came from
the unauthenticated GitHub API: 254,989 stars, 55,473 forks, MIT license, created 2025-07-22. History
came from the commits API on `tools/session_search_tool.py`.

How the sources were found:

- **Prior-art search.** `fleet-search web` returned only the docs site
  (`hermes-agent.nousresearch.com/docs`). SearXNG engines were suspended or behind a CAPTCHA, so the
  DeepSeek fallback served the results.
- **GitHub search was unavailable.** `fleet-search github` returned a 401 with no token, so the tree
  and file lists came straight from the API.
- **Not reachable this session.** WebFetch was not granted to this worker.

Unlike the other dissections in this series (WQ4349 IronClaw, WQ4354 Manus), this one is built from
**read source code**, not from search snippets. Every `path:line` below refers to that pinned commit.

Related work:

- Companion dissections: `research/WQ4337_ANTHROPIC_AGENT_DISSECTION.md` (§4, context management and
  the memory tool) and `design/wq3999_rex_skill_dissection.md` (REx@Skill, which led to Limpet's
  `injection.py`/`sanitize.py`).
- Limpet surfaces this doc maps onto: `design/wq3806_limpet_architecture.md` §5 (workspaces),
  `limpet/workspace_index.py`, `limpet/kb.py`.

---

## 0. Two corrections to the ticket's framing

1. **The "5+ tool calls" trigger no longer exists in code.** It survives only as a comment.
   - The old system-prompt sentence was "After completing a complex task (5+ tool calls)… save the
     approach as a skill". It was removed because Anthropic's server-side filter rejected it on
     subscription OAuth credentials and returned a billing-shaped HTTP 400
     (`agent/prompt_builder.py:242-253`).
   - The live guidance is one line: "When you work out a non-trivial workflow, record it with
     skill_manage for future reuse." (`agent/prompt_builder.py:259`).
   - The trigger that actually fires is a **counter of tool-calling iterations, default 10** (§1).
2. **Session search does no LLM summarization.** It was removed on 2026-05-18 in commit
   `abf1af54` (PR #27590). The commit message gives the reason: the auxiliary-LLM summary path "cost
   ~$0.30/call, took ~30s, and laundered FTS5 hits through a model that could confabulate when the
   right session wasn't in the hit list".
   - The replacement returns raw rows from SQLite in about 20 ms, against about 90 s for the old
     three auxiliary calls.
   - The README still says "FTS5 session search with LLM summarization", which is stale.
   - The module docstring is authoritative: "No LLM calls — every shape returns actual DB
     messages." (`tools/session_search_tool.py:8`).

These corrections matter for Limpet. The design Hermes moved away from, after paying for it, is the
one the ticket describes.

## 1. The closed learning loop: skill creation and self-improvement

**Trigger.** There are two counters on the agent, and both default to 10:

- **Skill counter.**
  - `agent._iters_since_skill` goes up by one on every tool-calling iteration, but only when
    `skill_manage` is in the toolset (`agent/turn_iteration_prep.py:165-167`).
  - The comment there says it "resets whenever skill_manage is used". Writing a skill yourself
    therefore resets the clock.
  - The threshold is `skills.creation_nudge_interval`, default 10 (`agent/agent_init.py:1388-1390`).
- **Memory counter.**
  - `agent._turns_since_memory` counts *user turns*, not tool calls (`agent/turn_context.py:758-765`).
  - The threshold is `memory.nudge_interval`, default 10 (`agent/agent_init.py:1304,1333`).
  - It is seeded from `prior_user_turns % interval` when a session resumes, so a long chat that is
    resumed does not restart at zero (`turn_context.py:754-755`).

The skill check runs **after the turn ends**, based on the iterations that turn used
(`agent/turn_finalizer.py:763-770`).

**Mechanism.** The review runs in a background fork, not as a nudge injected into the live prompt.

- **When it starts.** It starts only after the reply has been delivered, so it "never competes with
  the user's task".
  - It is skipped on interrupted turns.
  - It is skipped on cron runs: "the fork costs ~30K tokens / event with no human-in-the-loop
    benefit" (`agent/turn_finalizer.py:776-786`).
- **How it runs.** `_spawn_background_review` starts a daemon thread called `bg-review`
  (`run_agent.py:757-824`).
  - Children (`_delegate_depth > 0`) never spawn a review.
  - On the managed local llama-server, the review waits until the machine is idle
    (`run_agent.py:172-175`).
  - A preempted review is requeued up to 3 times (`run_agent.py:829`).
- **What the fork can do.** It is a full `AIAgent` with `max_iterations=16`
  (`agent/background_review.py:173`) and `skip_memory=True` (`:935`).
  - Its own nudge intervals are set to 0, so it cannot recurse (`:995`).
- **Prompt-cache trick.** The fork *advertises the parent's tool list byte for byte*, so its prompt
  prefix hits the parent's cache. Restrictions are enforced at dispatch time instead
  (`background_review.py:1090-1105`):
  - A skill-only review may call only `skills` tools plus read-only `read_file`/`search_files`.
  - `memory` is admitted only when the memory trigger fired.
  - The memory gate is spelled out "so an unattended fork cannot act on the memory tool's
    'consolidate now' hint and delete entries no one reviewed".

**What the review is told** (`_SKILL_REVIEW_PROMPT`, `background_review.py:448-517`). This is the
part worth copying, and it is the result of real tuning:

- **Be active.** "most sessions produce at least one skill update… A pass that does nothing is a
  missed learning opportunity."
- **Signals that warrant an update:**
  - The user corrected style or format.
  - The user corrected the workflow.
  - A non-trivial technique emerged.
  - "A skill that got loaded or consulted this session turned out to be wrong, missing a step, or
    outdated. Patch it NOW."
- **Order of preference:**
  1. Patch a skill that was **loaded** this session.
  2. Patch an existing umbrella skill.
  3. Add a support file under `references/`, `templates/` or `scripts/`.
  4. Only then, create a new **class-level** skill. Names must not be a PR number, an error string,
     or a "fix-X-today" session artifact.
- **Read-before-write is enforced by the tool.** A patch is refused unless the skill was loaded with
  `skill_view` *during this review*. A quote earlier in the transcript does not count (`:498-505`).
- **What a skill is** (`_LESSON_LAYER_BLOCK`, `:393-420`):
  - Procedure first.
  - Each pitfall is "a generalizable rule + one clause of WHY", not a narrative.
  - No ticket IDs, dates or quoted incidents.
  - Fix the wrong sentence in place; do not append "UPDATE: actually…".
- **What must not be captured** (`_DO_NOT_CAPTURE_BLOCK`, `:423-446`):
  - Environment-dependent failures.
  - Negative claims about tools ("these harden into refusals the agent cites against itself for
    months").
  - Transient errors.
  - One-off narratives.
  - **Unresolved failures written up as a "reliable workflow"**, which "presents an untested sequence
    of failures as validated guidance a future session will trust and repeat."

**The `skill_manage` tool** (`tools/skill_manager_tool.py`):

- **Actions:** `create`, `patch`, `delete`, `write_file` and `remove_file`, sent as an atomic
  `operations` batch that rolls back on any failure (`:722-728,826`).
- **On-disk format:** the agentskills.io layout, which is the same `SKILL.md` + YAML frontmatter
  format Claude Code skills use: `~/.hermes/skills/[category/]<name>/SKILL.md` plus
  `references/`, `templates/`, `scripts/` and `assets/` (`:4-7,113`).
- **Validation:**
  - `name` must match `^[a-z0-9][a-z0-9._-]*$` and be at most 64 characters.
  - The description is at most 1024 characters.
  - `SKILL.md` is at most 100,000 characters (`:108-112,146-179`).
- **Concurrency and audit:** every mutation takes a per-skill lock and is written to an append-only
  ledger (`:796-807`).
- **Deletes from a review:** a background review cannot hard-delete; it archives instead (`:583-593`).

**Self-improvement bookkeeping.**

- **Usage sidecar.** `~/.hermes/skills/.usage.json` records `created_by`, `use_count`,
  `view_count`, `patch_count`, `patch_generation`, `state` and `pinned`
  (`tools/skill_usage.py:1-6,348-353`).
- **Curator** (`agent/curator.py`). It runs when the machine is idle, not on cron.
  - Defaults: every 7 days, after at least 2 hours idle; a skill goes stale after 14 days unused and
    is archived after 30 (`:31-35`).
  - The active → stale → archived transitions are **deterministic, with no LLM**. Pinned skills and
    skills referenced by cron jobs are skipped (`:209-261`).
  - LLM consolidation into umbrella skills is opt-in (`DEFAULT_CONSOLIDATE = False`) and runs with no
    terminal access.

**Security of skills the agent writes.**

- `tools/skills_guard.py` is a regex scanner. It covers exfiltration, injection, persistence,
  obfuscation and other categories, plus invisible-Unicode, symlink and binary checks.
- For agent-created skills it is **off by default** (`skills.guard_agent_created`, default False,
  `skill_manager_tool.py:41-47`).
- The stated reason: "terminal() runs the same code ungated". If the agent can already run anything,
  gating what it writes to disk adds little.
- Even when the guard is on, the policy for agent-created skills is `("allow", "allow", "ask")` over
  safe/caution/dangerous (`skills_guard.py:27,32`). A "caution" verdict, meaning a high-severity
  finding, still passes. That is looser than the policy for installed `community` skills, which
  blocks at caution (`:30`).

## 2. Isolated subagents: isolated context, shared filesystem

**How a child is spawned.**

- It is an in-process `AIAgent` on its own single-worker executor thread
  (`tools/delegate_tool.py:279`, `tools/delegate_tool_child_run.py:848-865`).
- Its system prompt is built only from goal + context + a workspace hint.
- The parent's history is not passed. The tool description says "Children know nothing of this
  conversation".
- Context files and memory are skipped (`delegate_tool.py:246-284,645`).

**Blocked tools** (`tools/delegate_tool_toolsets.py:14-23`):

- `delegate_task` (no recursion)
- `clarify` (no user interaction)
- `memory` ("no writes to shared MEMORY.md")
- `send_message` (no cross-platform side effects)
- `cronjob_manage` (no scheduling work in the parent's name)

**Limits.**

- Depth is flat by default: `MAX_DEPTH = 1`, meaning parent → child only
  (`tools/delegate_tool_config.py:21`).
- `delegation.max_spawn_depth` can raise it, with "no hard ceiling".
- At most 10 concurrent children (`delegate_tool_config.py:17`).
- 250 iterations per child, each child with a fresh budget (`delegate_tool.py:105,291`).

**What returns to the parent** (`delegate_tool_child_run.py:592-616`):

- `summary`, which is the child's final text.
- `status`, `exit_reason`, `api_calls`, input/output `tokens`, `cost_usd` and `duration_seconds`.
- A `tool_trace` of tool names, argument summaries and byte counts.
- The child's intermediate steps never reach the parent's context.

**Filesystem isolation does not hold by default.**

- The child gets its own `task_id` and its own cwd record. But the code says "the child must share
  the PARENT's" container (`register_container_alias(child, parent)`, `delegate_tool_child_run.py:754-760`).
- Git-worktree isolation is opt-in, local-backend-only, and "failures degrade silently to the shared
  workspace" (`:385-404`).
- The only defence against cross-contamination is checked after the fact. When a child touches files
  the parent had read, the parent gets a note: "subagent modified files the parent previously
  read — re-read before editing" (`:971-990`).
- Timeouts are cooperative. A timed-out child keeps running on its daemon thread "until process
  exit", after the parent has been told it timed out (`:406-434`).

So the README's "prevents one workstream from contaminating another context or filesystem" holds for
**context**. For the filesystem it holds only if worktree mode is configured and actually works.

## 3. Agent-curated memory with periodic nudges

**Stores.**

- `~/.hermes/memories/MEMORY.md` holds agent notes, capped at 2,200 characters.
- `USER.md` holds the user profile, capped at 1,375 characters.
- Entries are separated by `\n§\n` (`tools/memory_tool.py:38-56`, `tools/memory_tool_store.py:25,186`).
- Actions are `add`, `patch`, `remove`, or an atomic batch.

**Injection into the prompt.**

- The system prompt gets a **snapshot frozen at session start**, described as
  "`_system_prompt_snapshot` is frozen at load time (prefix-cache stable)".
- Writes go to disk immediately but only appear in the next session (`memory_tool_store.py:176-178`).

**Guards on writes.**

- Every write gets a strict threat scan.
- Flagged entries load as `[BLOCKED: …]` in the snapshot (`memory_tool_store.py:221-253`).
- A background review may only `add`. `patch` and `remove` from an unattended fork are staged for
  human approval under `/memory pending` (`memory_tool.py:179-219`).

**Nudges.** Every 10 user turns, the background fork described in §1 runs `_MEMORY_REVIEW_PROMPT`:
"Review the conversation above and consider saving to memory if appropriate… If nothing is worth
saving, just say 'Nothing to save.'" (`background_review.py:383-388`).

**Flush points.** End-of-session extraction runs when the session id rotates (`/new`, compression),
and at shutdown (`run_agent.py:871-890`).

## 4. Cross-session recall: FTS5 without an LLM

**Indexes.** There are three FTS5 indexes over the `messages` table:

- `messages_fts` covers content, tool_name and tool_calls. Tool rows are truncated to a prefix.
- A trigram index excludes tool rows.
- A CJK bigram index (`tokenize='cjk_unicode61'`) loads from a shipped `.so`
  (`hermes_state_search.py:261-273`, `hermes_state_fts.py:40-103`).
- If corruption is detected, the triggers are dropped and search degrades to a `LIKE` scan
  (`hermes_state_fts.py:354-426`).

**Query path.**

- **Sanitizing:** balanced phrases are kept, FTS5 special characters are stripped, and dangling
  `AND`/`OR`/`NOT` are dropped (`hermes_state_search.py:818-852`).
- **Ranking:** BM25 `ORDER BY rank`.
- **Fallback chain:** if the plain query finds nothing, it tries CJK, then trigram, then the terms
  joined with `OR` (`:1162-1189`).
- **Results:**
  - Hits are grouped by session lineage, so a compressed chain of sessions counts once.
  - The default is 3 results, with a maximum of 10.
  - Cron sessions are ranked below interactive ones.
  - Subagent, kanban and tool sessions are hidden (`tools/session_search_tool.py:23,371-416,585`).

**What is returned.** Only the top hit is fully expanded:

- A **±5-message window** around the match.
- **Bookends:** the first 3 and last 3 user/assistant messages, which give "goal + resolution on
  every discovery hit".
- Size caps of 4,000 characters per message and 1,200 per bookend message (`session_search_tool.py:326-347,421`).
- To read further, the agent pages with `session_id` + `around_message_id` (about 1 ms per call).

## 5. Weaknesses seen in the code

1. **Persistent skill poisoning is lightly guarded** (§1).
   - Scanning of agent-written skills is opt-in, and "high" findings pass.
   - The Skills index tells the agent relevant skills "MUST" be loaded (`prompt_builder.py:1365-1366`).
   - So one bad skill is re-injected into every later session, and the curator only retires it after
     14–30 days unused.
2. **No real filesystem isolation between subagents** (§2).
   - Parallel children share the parent's container, and the worktree fallback fails silently.
   - Abandoned children keep running after the parent moves on.
3. **Subagent sessions are excluded from recall** (`session_search_tool.py:23`).
   - The only durable trace of a child's work is its self-reported summary.
   - The tool's own description warns that summary is unverified (`delegate_tool.py:647`).
4. **Code-shaped search terms lose meaning.** The sanitizer strips `/`, `+` and `#`
   (`hermes_state_search.py:38`), which damages queries like `C++`, `#123` and paths.
5. **The curator prompt sets a quota.** It says "fewer than 10 archives, you stopped too early"
   (`curator.py:443-446`), which pushes toward over-consolidation.

## 6. Gap check against Limpet today

| Hermes pillar | Limpet today | Gap |
|---|---|---|
| Skill library + review fork | Nothing. No `SKILL.md`/skill store in `limpet/`. The fleet's own Claude Code skills (`firmware-extraction`, `ghidra-re`, …) are hand-written. | No way to turn a finished RE workflow into a procedure. |
| Curated memory | Per-target `notes/` in the workspace (`design/wq3806_limpet_architecture.md:242`). No cross-target store. | No bounded, curated cross-target memory, and no nudge. |
| Isolated subagents | Per-workspace containers with no network; "The agent cannot reach one workspace's files from another" (wq3806:251-253). | Isolation is per workspace, with nothing inside a workspace for parallel workers. That is already stronger than Hermes between targets, but undefined within a target. |
| Cross-session recall | `kb.py` FTS5 over shipped static sources (`kb.py:223-224`). `workspace_index.py` FTS5 only over recovered **strings** (`workspace_index.py:115`). The `invocations` table (`:100-103`) has a `summary` column but no FTS. | No search over past *investigations*. Containment rules out reading other workspaces directly. |
| Injection hygiene | `injection.py`/`sanitize.py` (wq#4091) redact target-controlled strings before the agent reads them. | Covers what goes *into* the agent, not what the agent *writes to a skill*. |

## 7. Patterns applicable to Limpet (LIMPET VALUE)

The ticket's thesis holds: distilling a finished RE workflow into a reusable skill fits RE, where
the same chip family or bootloader recurs and planning is the expensive part. Adopt Hermes's
*mechanisms*, but invert two of its defaults.

1. **Trigger on an RE milestone, not a fixed number of turns.**
   - Hermes counts tool iterations (default 10) because a chat agent has no better boundary.
   - A Limpet workspace has one: committing a finding or exporting the evidence bundle
     (wq3806 §5). Run the skill review there, plus a fallback counter of ≥10 tool iterations since
     the last skill write. That fallback is the honest version of the ticket's "5+ tool calls".
   - Run it as a background fork *after* the operator has the result, never inline. On the local
     Ollama/llama.cpp box, follow Hermes's pattern of deferring until idle (`run_agent.py:172-175`).
2. **Copy the review prompt contract almost verbatim** (`background_review.py:393-517`). RE needs
   most of it:
   - **The "do not capture" rule for unresolved failures.** Most RE attempts fail, and a skill
     recording dead ends as a "workflow" is worse than no skill.
   - **Class-level naming:** a per-SoC family, bootloader or protocol, not per target serial.
   - **Read-before-write and patch-in-place**, which keep the library from becoming a pile of
     appended notes.
   - **Order of preference:** patch the loaded skill, then an umbrella, then a support file, and only
     then create a new skill.
   - Use the same agentskills.io `SKILL.md` format. Limpet skills then stay portable to Claude Code
     and the fleet's existing RE skills.
3. **Invert the guard default: agent-written skills are untrusted.**
   - In Limpet, a skill is distilled from a session that read *target-controlled strings*. A firmware
     author who can plant text has a path into a **persistent** prompt injection that survives into
     every later engagement. That is the weakness in §5.1 with an adversary attached.
   - Rule: every skill write runs through `injection.classify`/`is_injection`
     (`limpet/injection.py:108,124`), and a hit fails closed.
   - New agent-written skills land at the plugin trust gate's unpromoted tier (wq#4056/`_cmd_elevate`)
     until the operator promotes them.
   - Skills must not contain target-identifying data (serials, MACs, keys, hashes of customer
     artifacts). They are reused across customers.
4. **Fork pattern: advertise identical tools and restrict at dispatch** (`background_review.py:1090-1105`).
   - Advertising the same tools keeps the review fork's prompt prefix cache-identical to the parent's.
     That matters even more on local inference, where re-reading the prefix costs wall-clock time,
     not dollars.
   - Allow the skills tool and read-only file reads only. No terminal and no network.
5. **Subagents: use Hermes's blocked-tool list, but make filesystem isolation real.**
   - Adopt the blocked list (`delegate_tool_toolsets.py:14-23`): no recursion, no user interaction,
     no memory writes, no outbound messages, no scheduling.
   - Keep depth flat (1), cap at about 10 concurrent children, and use Hermes's result envelope
     (summary + `tool_trace` + tokens + exit_reason).
   - Do **not** adopt the shared-container default or the silent fallback.
   - Limpet already has `workspace.git`, so a parallel worker (for example, Ghidra on binary A while
     rizin runs on binary B) gets its own git worktree of the workspace and its own container. It
     **fails closed** if that cannot be set up.
   - A timeout kills the container rather than leaving it running (§5.2).
   - Child invocations go into the workspace `invocations` table, so a child's work stays
     searchable. That fixes §5.3.
6. **Recall: adopt Hermes's post-2026-05 design (no LLM), not the one the ticket describes.**
   - Hermes measured the LLM-summary path at $0.30 and 30–90 s per call, with confabulation risk.
     On an offline appliance running a local model it would be slower and no more accurate.
   - Build a **derived, read-only cross-workspace recall index** *outside* any workspace (for
     example `/var/limpet/recall.sqlite`). Feed it on each workspace commit from `manifest.json`
     target identity, the `invocations` summaries and the sanitized `notes/`.
   - Return Hermes's shape: BM25 hits grouped per workspace, a ±window around the match, and
     **bookends**. For RE, the bookends are target identity + first notes (the goal) and final
     findings (the resolution).
   - This keeps wq3806's containment rule intact, because no agent reads another workspace's files.
     It sees index rows written by the trusted indexer from already-sanitized content.
   - For queries, reuse `kb.py`'s quote-every-token sanitizer (`kb.py:279-280`), not Hermes's
     character stripping. RE queries are full of `0x8000_1234`, `sub_401000` and paths, which
     stripping would break (§5.4).
7. **Memory: bounded, frozen per session, and only humans delete.**
   - If Limpet adds a cross-target memory store (operator preferences, lab bench facts), copy
     Hermes's shape: a small character budget, a snapshot frozen at session start for cache
     stability, unattended forks limited to add-only, and patch/remove staged for operator approval.
   - The last rule matches the fleet's own Tier-0 rule that memory consolidation stays
     proposal-only (instinct 7).
   - Per-target facts stay in workspace `notes/`.

**Not adopting:**

- The messaging gateway (Telegram/Discord/…), Honcho user modelling, and cloud terminal backends
  (Modal/Daytona). They are tied to Hermes's personal-assistant role and do not fit an offline RE
  appliance.
- The LLM consolidation curator. Even Hermes ships it off by default. The deterministic
  stale/archive transitions (14 and 30 days, pinned skills exempt) are worth taking. The quota-driven
  consolidation prompt is not (§5.5).

**Suggested follow-up tickets** (not filed here; the board owner decides):

- (a) A `limpet-skills` store + review fork covering items 1–4 above.
- (b) A cross-workspace recall index, item 6.
- (c) A worktree-per-subagent inside a workspace, item 5.

Item (b) unblocks the "long-running RE investigation" use case on its own and has the smallest blast
radius, so it is the best one to start with.
