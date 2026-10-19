# wq#4337 — Dissection: Claude Computer Use + Claude Code (Anthropic agent architecture)

**Date:** 2026-10-19 · **Lane:** LIMPET research · **Sources:** Anthropic platform docs
(computer-use-tool, tool-use overview, context-editing), code.claude.com docs (security,
sandboxing.md, permission-modes.md — fetched 2026-10-19), Anthropic engineering blog
(claude-code-sandboxing). Doc URLs inline per section.

Companion to the other LIMPET dissections (see `research/WQ4211_EVOLVING_AGENTS_EVAL.md`,
`research/DISPATCH_DISSECTION.md`). Output: patterns applicable to the Limpet agent daemon
(`limpet/daemon.py` registry loop today; the planned HDMI-capture agent loop next).

---

## 1. Computer Use — how Claude drives a desktop

Source: https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool
(toolset `computer_toolset_20260801`, 17 member tools; older `computer_20251124` behind a
beta header).

**Architecture: the model never touches the machine.** Claude emits `tool_use` blocks
(`left_click`, `type`, `key`, `scroll`, `screenshot`, `zoom`, `wait`, …); *your*
application executes them against a virtual display and returns `tool_result` blocks.
Anthropic owns the brain, the integrator owns the hands. The loop:

1. Send prompt + tools. 2. Claude replies `stop_reason: "tool_use"` with ≥1 action blocks.
3. App executes each block **sequentially in order** (later actions depend on earlier UI
state — never concurrent). 4. Return one `tool_result` per `tool_use_id`. 5. Repeat until
Claude returns no tool calls; a `max_iterations` cap in the sampling loop prevents runaway
cost.

**Perception is a screenshot loop, not a live stream.** Claude requests `screenshot` when
it needs to see; batches conventionally *end* with a screenshot so the model verifies the
outcome of its own actions. Docs tell integrators: if a batch doesn't end with one, append
a screenshot to the last result anyway. Prompting guidance is explicit: "take a screenshot
after each step and confirm the outcome before continuing — Claude otherwise may assume
results." Error detection is therefore *visual self-verification*, not OS-level event
feedback.

**Coordinates.** All clicks are absolute pixel coordinates **in the pixel space of the
screenshots you returned** (origin top-left). If you downscale captures (recommended:
1024×768–1366×768, never above 1920×1080), you must scale Claude's coordinates back up
before injecting the click; Retina 2× is the classic offset bug. `zoom` captures a region
at full resolution for small text but does *not* change the coordinate space. Diagnostic
heuristic from the docs: clicks offset in one direction = unscaled coordinates; clicks
landing near-but-missing = resolution too low, use `zoom`.

**Batch/error protocol.** A response may carry several actions (click, type, screenshot).
Rules: stop at the first failure; return `is_error: true` with a clear message for the
failed block; every remaining block still gets a result with the exact text
`"Not executed: an earlier computer action in this turn failed."` (an unanswered
`tool_use` block rejects the whole request). Out-of-bounds coordinates and failed captures
are reported the same way — errors are *data returned to the model*, which then re-plans.
No hidden retry layer: the model is the retry logic.

**Screenshot budget.** ~1,000–1,800 tokens per screenshot; >20 images per request triggers
stricter per-image limits. Recommended pruning: keep the last ~3 screenshots and prune in
*batches* (e.g. every 25 turns), not every turn — per-turn pruning breaks the prompt-cache
prefix. Max image: 2576 px long edge / 4784 visual tokens on current models; the API
rejects oversized images rather than downscaling.

## 2. Claude Code — terminal agent

Sources: https://code.claude.com/docs/en/security, permission-modes.md, sandboxing.md.

**Planning.** No separate planner process — the model plans in-band. Structural supports:
*plan mode* (a permission mode: read-only exploration + classifier-approved commands,
produce a plan, user approves, then mode switches to execute); a todo-list tool for
multi-step task tracking; and subagents (`Task`/`Agent` tool) for fan-out, each with its
own context window, returning only a final report to the parent. Planning is a *permission
state*, not an architectural phase — the same loop runs throughout.

**Tool failure handling.** Failures return to the model as error text in the tool result;
the model adapts (same philosophy as Computer Use). The harness distinguishes
*deterministic denials* (allowlist miss, path outside boundary, hook block) from execution
failures, and the guidance baked into prompts is "a denial means re-scope, don't retry
verbatim" — we see this first-hand in fworker lanes.

**Permission model (ask vs auto).** Modes (`permission-modes.md` table): `default`
(manual: reads only auto-run; everything else prompts, with a built-in read-only command
set like `ls`/`cat`/`git status`), `acceptEdits` (+ file edits and fixed filesystem
commands in cwd), `plan`, `dontAsk` (pre-approved tools only, else deny — CI), and
`bypassPermissions` (everything — isolated VMs only). On top: user-configurable
allow/ask/deny rules, matched fail-closed (unmatched commands prompt), and command
injection detection (a command the parser can't fully analyze prompts even if an allow
rule matches part of it).

**Auto mode — the pattern most relevant to our gate.** A *second model* (the classifier,
Sonnet-class, separate from the session model) reviews actions instead of the user. Fixed
decision order (permission-modes.md L493–509): (1) explicit allow/ask/deny rules resolve
first — but protected-path writes and critical-path `rm` route to the classifier even when
allowed; (2) read-only actions and cwd edits auto-approve; (3) everything else goes to the
classifier; (4) a block returns the matched rule name (e.g. `[Data Exfiltration]`) to the
model as the reason. Hardening details worth copying:

- On entering auto mode, **broad allow rules are dropped** (`Bash(*)`, wildcarded
  interpreters, package-manager run commands) — narrow rules like `Bash(npm test)`
  survive. A blanket grant + autonomous classifier would be a bypass.
- Before a destructive command (`git reset --hard`, `rm -rf`) the harness itself runs
  `git status` and shows the classifier whether uncommitted work exists — *the gate
  gathers its own evidence* rather than trusting the actor's framing.
- **Tool results are stripped from classifier requests** so hostile file/web content can't
  prompt-inject the judge; a separate server-side probe scans incoming tool results before
  the main model reads them.
- Conversational boundaries ("don't push until I review") are block signals the classifier
  re-reads from the transcript each check; approvals must name the action *and* the
  specific danger ("you can force-push" alone clears nothing), and cover one action.
- Fallbacks fail closed: no verdict → deny; 3 consecutive or 20 total blocks → pause auto
  mode and resume human prompting; headless runs just skip the action and keep working.
- Subagents are checked at three points: task description at spawn, each action during the
  run, and the final report before the parent reads it (flagged reports are delivered with
  a prepended security warning, not silently dropped).

**Sandboxing file/command operations** (sandboxing.md; engineering post
anthropic.com/engineering/claude-code-sandboxing — "sandboxing safely reduces permission
prompts by 84%"). OS-enforced, not prompt-enforced: macOS Seatbelt; Linux/WSL2
`bubblewrap` (filesystem namespace, deny-by-default writes outside allowed dirs) +
`socat` relaying all traffic through a local proxy that checks each host against an
allowed-domain list (starts empty). The trade: inside the boundary commands run *without
prompts*; at the boundary the command fails (`Operation not permitted` /
`Could not resolve host`) and Claude may request an **unsandboxed retry**, which is a
normal permission prompt — the escape hatch is explicit and user-gated
(`allowUnsandboxedCommands` can disable it). Scope caveat: the sandbox covers shell
commands only; file tools, MCP servers, and hooks run outside it, governed by the
permission layer. `sandbox.failIfUnavailable=true` makes missing deps fatal instead of
silently degrading. File tools separately respect a working-directory boundary
(cwd + `--add-dir` roots; first read outside prompts; symlink targets re-checked).

## 3. Tool use architecture

Sources: https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview,
/handle-tool-calls, /parallel-tool-use.

- **Contract:** tools are JSON Schema definitions (`name`, `description`, `input_schema`);
  descriptions drive tool selection. `strict: true` guarantees schema-exact calls.
  `tool_choice`: `auto` / `any` / specific `tool` / `none`.
- **Client vs server tools:** client tools (bash, text editor, computer use, user-defined)
  execute in the integrator's app; server tools (web_search, code_execution, tool_search)
  execute on Anthropic infra and short-circuit the round trip.
- **Parallel vs sequential:** the model may emit multiple `tool_use` blocks per response.
  *Independent* calls (parallel reads/searches) can be executed concurrently by the
  harness; UI-state-dependent batches (computer use) must run in order. All results return
  in a single `user` message, one `tool_result` per `tool_use_id` — any unanswered id is
  an API error. Integrators can force one-call-per-turn with
  `tool_choice: {disable_parallel_tool_use: true}`.
- **Errors:** `is_error: true` + message text. No schema for errors — plain prose the
  model can reason about. Retry logic lives in the model (it sees the error and chooses),
  not in a mechanical retry wrapper; harness-level retry exists only for API transport
  (429/5xx backoff in SDKs).
- **Scale pattern:** tool_search / deferred tools — ship names only, load schemas on
  demand, keeping hundreds of tools out of the prompt until needed.

## 4. Context management for long sessions

Source: https://platform.claude.com/docs/en/build-with-claude/context-editing.

Four composable layers, all threshold-triggered so short sessions pay nothing:

1. **Tool result clearing** (server-side, `clear_tool_uses_20250919`): past a token
   trigger (default 100k input tokens), oldest tool results are replaced by placeholders;
   keep N most recent (default 3); `clear_at_least` ensures each clear removes enough to
   justify the cache invalidation it causes. Tool *inputs* stay visible by default.
2. **Thinking block clearing**: keep last N turns' reasoning blocks.
3. **Compaction** (client SDK or server-side `compact_20260112`): model summarizes its own
   history into `<summary>` tags; history is *replaced* by the summary (~100k → 2–3k
   tokens) and work resumes. Claude Code's auto-compact is this pattern; its session
   guidance promises the summary plus remaining context carries into the next window so
   agents don't wrap up early.
4. **Memory tool** (`memory_20250818`): files outside the context window. Key interlock:
   as context approaches the clearing threshold the model gets an automatic warning so it
   can persist findings *before* the eviction destroys them.

Also structural: subagents isolate context per task (parent sees reports, not transcripts)
and prompt-cache discipline shapes everything (append-only prefix; prune/clear in batches;
frozen system prompt/tools mid-session — same reason our lanes freeze CLAUDE.md,
`research/PREFIX_STABILITY.md`).

## 5. Safety / guardrails — compared to our gate

Anthropic's stack is layered: (a) OS-enforced sandbox (Seatbelt/bubblewrap + network
proxy); (b) rule layer (allow/ask/deny, fail-closed, injection-suspicious commands always
prompt); (c) model judge (auto-mode classifier with fixed precedence, evidence-gathering,
stripped tool results, fail-closed fallbacks, block-streak circuit breaker); (d)
server-side probes scanning tool results for injection before the model reads them; (e)
human prompts as the residual layer. Computer-use adds product-level guidance: isolated
VM, credential withholding (XML-tagged if unavoidable), domain allowlists, human
confirmation for consequential actions, injection classifiers on screenshots.

Our fworker gate (PreToolUse guard hook + per-command allowlist + path bounds) maps to
layer (b) only, with the busy-key/evidence harness as process control. What Anthropic has
that we lack: an OS boundary *under* the rules (a guard hook bypass is game over for us;
their bubblewrap isn't), a model judge for the gray zone between "allowlisted" and
"denied" (our equivalent today is a hard deny + redispatch), evidence-gathering before
destructive ops, and the block-streak circuit breaker (we have the loop breaker, which is
the same idea on the actor side rather than the judge side).

## Patterns applicable to the Limpet agent daemon

Limpet today: `limpet/daemon.py` is a reconcile timer (registry health loop), with
`critic.py`/`obligations.py` as the quality/obligation gate and `model_router.py` for
model selection. The HDMI-capture agent (agent watches a target device's screen and drives
it over USB-HID/serial — a 1:1 structural match for Computer Use) should adopt:

1. **Brain/hands split.** The LLM emits typed action requests; a Limpet executor validates
   and performs them against the capture/HID layer and returns typed results. The model
   never owns the transport. This makes the gate a chokepoint by construction.
2. **Screenshot-loop perception with forced verification.** End every action batch with a
   capture; prompt the model to confirm outcome before the next step ("Claude otherwise
   may assume results" — equally true of a target device that dropped a keystroke).
   Budget: downscale captures to ≤1366×768, keep last ~3 frames, prune in batches of ~25
   turns to preserve the prompt cache, never >20 images in flight.
3. **Coordinate-space contract.** All click/touch coordinates are in the pixel space of
   the frames *we return*; scale factors live in the executor, never in the model's head.
   Keep a `zoom` equivalent (region crop at native resolution) for small UI text on the
   target — cheaper than raising global capture resolution.
4. **Batch halt-on-failure protocol.** Execute action batches strictly in order; first
   failure stops the batch; every skipped action still gets an explicit "not executed"
   result. Errors are prose returned to the model — the model is the retry logic; cap
   iterations in the loop, not retries per call.
5. **Two-model gate (classifier pattern) for autonomous runs.** Route gray-zone actions
   (anything not on a narrow allowlist, writes to the target's persistent storage,
   network-touching ops) through a cheap judge model with: fixed precedence order; broad
   allow rules dropped in autonomous mode; tool results stripped from judge input
   (anti-injection); fail-closed on no-verdict; 3-consecutive/20-total block circuit
   breaker that downgrades to blocked-ticket instead of looping.
6. **Gate gathers its own evidence.** Before a destructive target op (reflash, factory
   reset, partition write), the executor itself captures device state (current firmware
   version, mounted volumes, dirty flags) and hands it to the judge — don't trust the
   actor's framing. Direct analog of Claude Code's pre-`rm -rf` `git status`.
7. **OS boundary under the rule layer.** Run Limpet tool subprocesses under bubblewrap
   with deny-by-default writes + a socat/proxy for network (allowed-domain list starts
   empty), and make "retry unsandboxed" an explicit, logged, human-gated escape hatch.
   Our guard hook then becomes defense-in-depth rather than the only wall.
   `failIfUnavailable` semantics: if bwrap is missing, refuse to start autonomous mode
   rather than silently running open.
8. **Context layers for long RE sessions.** Threshold-triggered tool-result eviction
   (keep last 3, clear ≥5k tokens per pass), compaction summaries, and a memory-file tool
   with the *pre-eviction warning* interlock so the agent persists findings before they're
   cleared. Subagents for fan-out (per-binary analysis) returning reports, not transcripts.
9. **Credential hygiene on target devices.** If the agent must log into a target UI, pass
   credentials in tagged blocks, treat login flows as elevated injection risk, and require
   confirmation for consequential actions (anything that writes to the device or accepts
   ToS) — per Anthropic's computer-use safety list.

**Non-adoptions (considered, rejected):** per-turn screenshot pruning (breaks prompt
cache); mechanical retry wrappers around tool calls (model-mediated retry is strictly more
informed); a monolithic always-on judge for *every* action (Anthropic auto-approves
read-only + rule-matched actions and reserves the classifier for the gray zone — judge
cost/latency only where it buys safety).
