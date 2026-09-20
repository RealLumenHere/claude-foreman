# DSH_EVAL — adopt DeepSeek Harness (dsh) for the scout / intel / signal-engine pipelines?

_2026-09-18 PDT · session orch-deepseek-agent-eval-3e852a · RESEARCH + STAGED KIT ONLY (nothing installed, no service/config/DB touched)_
_Evidence grades: **[V]** verified this session (command/URL in §9) · **[R]** read in upstream source/docs at a pinned commit, not executed · **[U]** unverifiable without an install_
_Sibling: AGENTFORGE_EVAL.md (same day). This report reaches the same place by a different road, and that is a finding in itself (§6)._

## 0. VERDICT (read this, skip the rest if busy)

**Do not adopt dsh as the runtime for these pipelines. Do not replace LangGraph. Mine five patterns. One optional,
jailed sidecar pilot is staged but gated on FRB-v1 (wq#2035). Final call belongs to the Orchestrator: wq decision ticket in §8.**

1. **The premise is half wrong.** `lumen-scout` IS on a framework: `autonomy/lumen_scout_graph.py` is a LangGraph
   `StateGraph` (fetch -> score -> filter -> write), and so is the fleet researcher it feeds (`lumen_fleet_graph.py`,
   langgraph 1.2.5). `lumen_intel.py` (680 ln) and `signal_engine.py` (301 ln) make **zero LLM calls**: they are
   deterministic fetch -> keyword-score -> dedupe -> write-a-prompt-file ETL. There is no agent loop in them to replace. **[V]**
2. **dsh is the wrong shape and the wrong maturity for a timer-driven batch job.** It is a TypeScript/Node interactive
   coding-agent runtime (workspace + shell + file tools + Web UI). HEAD is `0.1.6-alpha.2`; 22 npm versions in 38 days;
   three on-disk session-format migrations (v0->v3) in five weeks; README in capitals: THERE WILL BE
   COMPATIBILITY-BREAKING CHANGES; SAFETY.md: no security audit, not production-ready. **[V]**
3. **Data governance is the disqualifier for unattended use.** Every shipped profile, including `sdk-minimal`, mounts two
   default-ON contributors that ride DeepSeek requests "including requests sent through configured gateways":
   `dsh_session_log` (the complete un-acked session-log suffix: prompts, tool args, tool results, workspace paths) and
   `dsh_plugin_packages`. Base also ships OTel `FEEDBACK_ONLY` to `harness-telemetry.deepseeksvc.com`. The default
   flipped three times (07-31 mount, 08-10 off, 08-25 on). An opt-out you must re-verify on every alpha bump is not
   an opt-out. **[V — read at HEAD ddefc45]**
4. **The real quality lever is downstream, and it is small.** The researcher is single-shot: one Tavily context prepend,
   one LLM call (`max_tokens=2500`), no tool loop. That is the class behind AGENTFORGE_EVAL §0.2 ("the fleet research
   note invented a repo tree that does not exist"). A bounded verify-hop (fetch the cited repo/paper before writing about
   it) closes that class and is native to the LangGraph already running. It does not need a second runtime. **[V]**
5. **Found while auditing, unrelated to dsh: `lumen-signal-engine` is dead-green.** exit 0 daily, 1 prompt submitted
   since June. Its only sink (`lumen-vip-api` :8099) was retired 08-28; Reddit's public JSON returns 403; §7. **[V]**

## 1. WHAT dsh ACTUALLY IS

| Fact | Value | Grade |
|---|---|---|
| Repo | github.com/deepseek-ai/deepseek-harness, MIT, created 2026-08-13 (same day as V4-Pro GA) | V |
| Traction | 229,219 stars / 27,433 forks (GitHub API). Issues disabled (open_issues=0), Discussions only | V |
| Version | root `0.1.6-alpha.2` @ ddefc45 (2026-09-17). PyPI `deepseek-harness-sdk` lags at `0.1.5rc1` | V |
| Stack | TypeScript monorepo, pnpm 11.7, Node `^22.19 \|\| >=24` (Lumen: node 22.23.2 OK, pnpm absent). 158 MB shallow | V |
| Core idea | Cordis plugin micro-kernel (source-vendored, MIT). Model adapter, tools, session log, sandbox, UI and the agent loop itself are rows in a layered `cordis.patch.yml` | R |
| Run modes | `dsh web` (UI :3080), `dsh --profile headless "<task>"` (one-shot, exit 0/1), SDK = stdio JSON-RPC subprocess, ACP | R |
| Python | `pip install deepseek-harness-sdk` bundles the runtime (no system Node); `DeepSeekHarness(...).run()` | R |
| Models | native DeepSeek adapter (`messages` or `chat-completions`, `baseURL` overridable) + pi-ai multi-provider / OpenAI-compatible routes | R |
| Sandbox (Linux) | bwrap then Landlock, fails closed if neither. `sdk-minimal` pins `danger-full-access` | R |
| Footprint | RSS / cold start | U |

## 2. FIT — pipeline by pipeline

| Pipeline | What it is today | LLM calls | What dsh would replace | Verdict |
|---|---|---|---|---|
| lumen-scout (daily 06:00Z) | LangGraph 4-node graph over GitHub search, 14 topics; 30 gap prompts this ISO week; 2.6 s CPU | 0 | nothing: no model, no tools, no session | NO |
| lumen-intel (every 4 h) | 11 fetchers (arXiv 7,026 seen / Decoder 923 / GitHub 535 / OpenAlex 461 ...) -> keyword score -> prompt file; 8.5 s CPU | 0 | nothing | NO |
| lumen-signal-engine (daily) | Reddit/HN/competitor RSS -> POST to a retired endpoint | 0 | nothing; it needs a sink, not a framework (§7) | NO — fix or retire |
| fleet researcher (downstream) | LangGraph: load -> web_search -> local_retrieval -> call_llm -> quality_gate -> verify_opt -> write; 133 outputs/24 h, 756/7 d | 1 per task (+retry) | the single `call_llm` node could become a tool loop | the only place an agent loop earns anything; build it in LangGraph (§3 M1) |

Wrapping a 2-second deterministic cron job in an event-sourced agent runtime buys a Node dependency, a plugin loader and
a telemetry surface, and changes zero outputs. "Unified framework" is already true where it matters: the graph-shaped
stages are on LangGraph + Langfuse + LiteLLM. The un-unified part is that intel/signal are plain scripts, and that is fine.

**Call-volume reality for any tool loop.** Researchers run on free tiers (Cerebras / SambaNova / Mistral / OpenRouter).
133 tasks/day at ~1 call each today. A general agent loop at 6-10 model calls per task is ~800-1,300 calls/day against
the same rate limits (estimate; the multiplier is what the pilot measures). A bounded 2-hop verify is ~3x, not ~10x.

## 3. WHAT TO MINE — ranked, each mapped to a Lumen target (design-first; nothing built)

All five are patterns, not code lifts: dsh is TypeScript on Cordis, the fleet is Python on LangGraph. MIT permits reuse.
**Every item that touches the researcher graph is blocked on FRB-v1** ("No researcher prompt/playbook/graph change may
ship until this exists", wq#2026).

| # | Pattern (upstream source) | What it is | Lumen target | Why it ranks here |
|---|---|---|---|---|
| M1 | bounded tool loop + `guard/repeat-tool-reminder` + `guard/timeout-policy` | agent step loop; advisory nudge at 3/5/8 identical tool calls (never blocks); per-tool cooperative timeout surfaced to the model as a clear error | new `verify_hop` node in `lumen_fleet_graph.py`: for each cited repo/paper, fetch the primary page once, strike or mark unverified what does not resolve. Hard caps: 2 hops, N fetches, wall-clock | kills the fabricated-entity class at its source; directly moves FRB M1. Generalises the arXiv-only `verify_opt` that already exists |
| M2 | `core/session` append-only event log | every model-visible fact (prompt, tool call, tool result, response) is an ordered durable event; history is DERIVED from the log; replay/fork/inspect | per-task JSONL run log written by the graph (one line per node transition, full tool payloads) | this IS the substrate FRB-v1 §3.2 needs ("snapshotted web+local retrieval replay", `FRB_REPLAY=1`). Build once, serve both |
| M3 | `spill/spill-policy` + `compaction-tool-result-pruner` | oversized tool text -> bounded head/tail preview + locator; full text stays retrievable; no model call | wrap `fleet_search.search_context` + the M1 fetcher | prerequisite for M1: one fetched README can exceed the whole free-tier context. Cheap, deterministic |
| M4 | `session/session-checkpoint-policy` | durable write BEFORE a model request and BEFORE any tool side effect; checkpoint failure is fail-closed; an interrupted tool recovers as "unknown outcome", never auto-retried | LangGraph checkpointer (SqliteSaver) on the fleet graph, which compiles with none today | same fail-closed doctrine as the verify gate; makes a worker crash resumable instead of requeue-from-zero. Lower value while tasks are ~1 call |
| M5 | Minimal mode (2 tools, fixed one-line system prompt) used for DeepSeek's own published benchmarks | measure the model through the thinnest possible harness so the harness is not the variable | FRB-v1 runner design | a principle, costs nothing: keep the FRB harness minimal and frozen |

Looked at and deliberately NOT mined: `llm-retry` (LiteLLM already owns retry/fallback; a second layer is the silent-fallback
pathology again) · `webhook` -> session rules (no webhook source in these pipelines) · `workflow`/`ralph`/`subagent-*`
(fan-out orchestration = foreman + wq territory, already built and hardened) · `goal` round-driver (same) · Cordis
patch-layer config with per-row provenance in `--dump-config` (genuinely nice; not worth a config-system rewrite) ·
`hooks-claude-code` bridge (fworker harness already has action_gate).

## 4. RISKS OF RUNTIME ADOPTION (why §0 says no)

1. **Exfil-by-default** (§0.3). Mitigable per-version with `lockdown.patch.yml` + `DSH_TELEMETRY_DISABLED=1` + a kernel
   egress jail, but it converts every upstream alpha bump into a security review. Intel prompts are low-sensitivity;
   a `danger-full-access` shell on a box with NOPASSWD sudo is not.
2. **Churn.** 22 releases / 38 days, 3 session-format generations, PyPI and npm out of step. Pin-and-freeze works for a
   pilot; for a timer that must run for a year it means owning a fork.
3. **Second runtime.** Node + pnpm + a plugin loader beside Python/LangGraph/Langfuse. Langfuse node-level tracing, the
   LiteLLM key-per-service model, the fail-closed verify gate and the dup-canary would all need re-plumbing. Negative value,
   same conclusion AGENTFORGE_EVAL reached for a LangGraph wrapper.
4. **Model fit.** dsh's loop is tuned for DeepSeek V4 tool-calling. The researchers run free-tier Llama/GPT-OSS/Mistral
   class models; DeepSeek is under austerity and `deepseek-v4-flash` had a 59% connect-failure ticket (wq#1590).
   Tool-loop reliability on the models actually available is [U].
5. **Supply chain.** 316+ community plugins, a third-party same-name Python `dsh` CLI holding the PyPI names `deepseek-harness` /
   `deepseek-harness-cli` (not DeepSeek's; the official wheel is `deepseek-harness-sdk`), installs that run `prepare` scripts. None of it is needed; all of it is one typo away.

## 5. OPTIONS FOR THE ORCHESTRATOR

| Opt | What | Cost | Risk | Recommendation |
|---|---|---|---|---|
| **A** | Reject runtime adoption. Mine M1-M5 into the existing LangGraph harness, in the order M2 -> M3 -> M1 -> M4, all behind FRB-v1 | design doc + ~4 build tickets | low; every step is measured | **RECOMMENDED** |
| B | A + run the staged dsh sidecar pilot on hathor as a measuring stick for "how much does a mature tool loop help" before writing M1 | ~half a session + a LiteLLM virtual key | contained by the egress jail; throwaway | optional; only worth it if M1's design is contested |
| C | Adopt dsh as the pipeline runtime (headless profile under the timers, or SDK sidecar in production) | large, recurring | §4 all five | REJECT |

## 6. THE PATTERN BEHIND THIS TICKET

Two framework-adoption evaluations in one day (AgentForge, dsh), both resolving to "already on LangGraph, build FRB-v1".
FRB-v1 has now been deferred twice and is filed twice (wq#2026 held 48 h, wq#2035 open p1 — same normalized intent,
different titles, so `wq add` dedupe did not catch it). Until a frozen benchmark exists every "adopt X" proposal costs a
research session and ends in a judgment call. **The highest-leverage output of this evaluation is not about dsh: it is
that wq#2035 should be the next thing dispatched.**

## 7. DEFECT FOUND: lumen-signal-engine is dead-green (product-liveness FAIL)

- Submitted prompts by month: May 68 · Jun 1 · Jul 3 · Aug 1 · Sep 0. Unit Result=success every day. **[V]**
- Sink gone: `signal_engine.py:20` posts to `http://localhost:8099/vip`; nothing listens on :8099; no vip unit exists;
  claude_memory.md:5365 records `lumen-vip-api` retired 2026-08-28. 12 "Connection refused" lines since 09-05. **[V]**
- Source gone: `https://www.reddit.com/r/<sub>/hot.json` returns **403** from Lumen with the script's UA; the code
  `continue`s on non-200, so it logs "Reddit signals: 0" forever. HN and competitor RSS also 0 on the last run. **[V]**
- Second copy of the class: `lumen_self_healer.py:57` still port-checks `lumen-vip-api: 8099`. **[V]**
- Log lines are doubled (StandardOutput=append + the script's own file logging). Cosmetic, same file.
- CLASS: a producer outliving its consumer, with fail-open `continue` hiding a dead source. exit 0 != forward progress.
  Decision is retire-or-rewire (sia-news / the `news_*` lane already covers live news and DID catch dsh on launch).
  Not fixed here by instruction. Ticketed §8.

## 8. ACTIONS TAKEN + HANDOFF

No installs, no pip/npm/pnpm, no service, timer, unit, config or DB change. Read-only except:
- `/home/lumen/foreman/research/DSH_EVAL.md` (this file)
- `/home/lumen/scratch/dsh_eval/src/` shallow clone @ ddefc45 (158 MB, read-only reference; safe to delete)
- `/home/lumen/scratch/dsh_eval/pilot_kit/` README + `lockdown.patch.yml` + `litellm-route.patch.yml` + `pilot.env.example`
  (YAML parse-checked; never booted; grammar verified against upstream `patch.ts`)
- wq tickets: see the ticket ids appended below by the session.

## 9. SOURCES / VERIFICATION LOG

- Pipelines: `systemctl cat/show lumen-{scout,intel,signal-engine}`; `grep -nE 'requests\.post|completions|:4000'` over
  the three scripts (only hit: signal_engine.py:79 -> VIP_API); `journalctl -u lumen-scout/-intel`; `signal_engine.log`
  monthly fold; `ss -ltn | grep 8099` (empty); `curl -A lumen-signal-engine/1.0 .../r/selfhosted/hot.json` -> 403;
  `intel.db seen_items` group-by; `find research_prompts_done -name '00_gap_*'` per ISO week; `scratch/research` mtime counts.
- dsh: `git clone --depth 1` -> HEAD ddefc45fbc7f 2026-09-17T21:19+08:00; root package.json; `apps/cli/reference/README.md:110-112`;
  `packages/session/session-log-deepseek/README.md`; `packages/llm/plugin-package-inventory-deepseek/README.md`;
  `packages/bundle/{base,sdk-minimal}/cordis.patch.yml`; `.agents/notes/implemented/feature/2026-08-25-feedback-gated-telemetry-default.md`;
  `docs/user/guide/python-sdk.md:180`; `packages/boot/plugin-manager/src/patch.ts`; `packages/llm/llm-deepseek/README.md` +
  `src/protocols/chat-completions/adapter.ts:330`; `packages/sandbox/sandbox-local/README.md`; SAFETY.md; THIRD_PARTY_NOTICES.md.
- Registry/API: `npm view @deepseek-ai/dsh versions/time`; `pypi.org/pypi/deepseek-harness-sdk/json`;
  `api.github.com/repos/deepseek-ai/deepseek-harness`.
- NOTE on stale web copies: github.com's rendered `apps/cli/reference/README.md` served an OLDER revision ("telemetry
  stays local by default", 31.4k stars) than the clone and the API (default-on upload, 229k stars). Launch-week blog
  coverage predates the 08-25 flip. Trust the pinned clone, not the prose. Same lesson as DESIGN-DOC HALF-LIFE.
- Lumen side: `litellm_config.yaml` aliases + `drop_params: true` (line 340); langgraph 1.2.5; node v22.23.2; pnpm absent.

## 10. TICKETS + POST-FILING FINDING (appended 2026-09-18 ~20:05 PDT)

- **wq#2038** `[do-not-claim]` ORCH DECISION, Option A/B/C — router: claude-fable-5 @high. Title tag matches the dispatcher's
  `NODISPATCH_TITLE_RE`, so no fworker takes it; it waits for an orchestrator session.
- **wq#2039** signal-engine dead-green, retire or rewire — router: claude-opus-5 @xhigh.
- **wq#2040** `[design]` verify-hop + event log (M1-M3), `--deps 2035` — router: claude-fable-5 @high.
- Episode `cf4042d5d8b7`.

**The keystone cannot dispatch as written. [V]** `wq#2035` (FRB-v1, p1) is skipped on every dispatcher pass:
`stage=capability reason=needs_unresolved detail="'sudo' is a v2 verb: no fleet-exec grant exists for it"`. The trigger
is a prose mention in its detail ("a box where agents have sudo"); the ticket also has a real privileged step (new unix
user `frb`), so no fworker can ever take it whole. It needs splitting: design doc (no privileged verb, broker-dispatchable)
+ a privileged install step run by an orchestrator session under lumen-change-safety. Noted on #2035 and #2038.
Class size: 40 distinct open tickets currently skip as `needs_unresolved` (pass_end candidates=140, dispatchable=13);
structural skips are excluded from the stall watchdog, so none of them page. Datapoint added to open wq#1697.
