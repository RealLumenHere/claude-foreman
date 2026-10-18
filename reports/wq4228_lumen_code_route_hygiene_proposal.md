# wq#4228 — `lumen/code` route hygiene: proposal

Source ticket: wq#4228 (from wq#4207). Evidence: `/home/lumen/scratch/research/gptme-evaluation.md` §4–5.
Status: **PROPOSAL ONLY.** Nothing in `/home/lumen/agent/litellm_config.yaml` or on isis was changed.
Every edit below goes through the G2 change-safety gate: precheck probe, then apply, then a
post-apply probe that can fail. That is the same shape as wq#4213's `reload_and_verify.sh`.

## 0. What I verified (2026-10-18, ~08:35Z, read-only)

| Fact | Evidence |
|---|---|
| `lumen/code` routes to `ollama/qwen3.6:35b-a3b` @ `http://100.100.129.24:11434` but its description says `qwen2.5-coder:14b on isis` | `litellm_config.yaml:394-404` |
| **Same stale description on a second route**: `isis-coder-14b` also routes to `ollama/qwen3.6:35b-a3b`, and its description says `qwen2.5-coder:14b Q4_K_M ... 16384 ctx` | `litellm_config.yaml:344-355` |
| No route that hits isis Ollama sets `num_ctx` (`lumen/code`, `isis-coder-14b`, `local-worker`→`openai/isis-coder`, `qwen-local`→`openai/qwen2.5-coder:7b`) | `grep -n num_ctx litellm_config.yaml` → no hits |
| isis serves Ollama **0.34.4**, not a standalone llama-server. Ollama spawns its own llama runner, so the "llama-server restarted" in wq#4207 was an Ollama runner reload | `GET /api/version` |
| `qwen3.6:35b-a3b` model record was **modified at 2026-10-18T07:57:49Z**. That falls between the 4096 reading (~07:47Z) and the 32768 reading (~08:06Z). Someone re-created or re-pulled the model with a new Modelfile; it was not only a restart | `GET /api/tags` |
| Current Modelfile now carries `PARAMETER num_ctx 32768`; loaded `context_length: 32768`; native max 262144 | `POST /api/show`, `GET /api/ps` |
| Loaded with **`size_vram: 0`**, so the 35B MoE runs entirely on CPU/RAM. That accounts for the 63 tok/s prefill and the 3–7 min cold turns | `GET /api/ps` |
| Ollama reports `capabilities: [completion, vision, tools, thinking]`, so the model does support native tool calls. The text-mangled calls point at the route or provider path, not at a model with no tool support (see §3, H1) | `POST /api/show` |

Who re-created the model at 07:57Z was not found in `/home/lumen/logs`, `agents/work-queue` or
`foreman/reports`. That does not affect the fix: the fix is to stop depending on whoever touched the
Modelfile last.

## 1. Fix the descriptions

Change only `model_info.description` on both entries. This does not affect routing:

```yaml
# lumen/code (litellm_config.yaml:404)
description: qwen3.6:35b-a3b (MoE, Q4_K_M, CPU-only) on isis (ollama), primary code model (wq#3794); num_ctx pinned 32768 (wq#4228)
# isis-coder-14b (litellm_config.yaml:354-355)
description: alias of lumen/code — qwen3.6:35b-a3b on isis (ollama), NOT qwen2.5-coder:14b despite the name; num_ctx pinned 32768 (wq#4228)
```

`isis-coder-14b` is misnamed as well as misdescribed. Renaming a model group breaks its callers, so a
rename is out of scope here. Run `grep -rn isis-coder-14b` across the fleet before anyone renames it.

## 2. Pin `num_ctx`

Ollama sets the context per runner. A request whose `options.num_ctx` differs from the loaded runner
**reloads** it. A request without `num_ctx` gets the Modelfile default. If neither is set, the server
default applies (4096 on older builds). A prompt longer than the window is **silently truncated** from
the front: the system prompt and tool schemas are lost first, and no error is raised. That fits T1–T3
in wq#4207.

Pin the value at three layers, so no single actor (a Modelfile rewrite, another caller, a server
restart) can change it silently:

1. **LiteLLM, per route (primary).** Add `num_ctx: 32768` to `litellm_params` of **both**
   `lumen/code` and `isis-coder-14b`. They must use the same value. If they differ, two routes to one
   model keep reloading the runner. LiteLLM's Ollama provider forwards `num_ctx` into Ollama
   `options` for `ollama/` and `ollama_chat/` models. Confirm this at apply time with the post-probe
   below, not on trust.
2. **Modelfile (default for callers that bypass LiteLLM).** It already holds `PARAMETER num_ctx
   32768` since 07:57Z. Record that Modelfile in the repo (e.g. `ops/isis/Modelfile.qwen3.6`), so the
   next `ollama create` reproduces it and doesn't regress it.
3. **Server floor.** Set `OLLAMA_CONTEXT_LENGTH=32768` in the isis `ollama.service` drop-in. This
   restarts Ollama on isis, which is allowed (only Thor is restart-protected). Do it in a window,
   because the route is single-slot and shared fleet-wide.

Caveat: the `openai/...` routes against `:11434/v1` (`local-worker`, `qwen-local`) cannot pass
`num_ctx`, because Ollama's OpenAI-compat endpoint ignores it. They get layer 2 or 3. Either move them
to `ollama_chat/` with `num_ctx`, or rely on the server floor.

Make overflow loud instead of silent: set `model_info.max_input_tokens: 30720` on both routes, and in
`router_settings` set `enable_pre_call_checks: true` so LiteLLM rejects oversize prompts before they
are truncated. Check the flag name against the running LiteLLM version at apply time.

**Why 32768:** gptme's prompt is ~11.1k tokens, Claude-Code-style harnesses run 20–30k, and this fits
in RAM on a CPU-only box. Going higher costs prefill time linearly at 63 tok/s (32k cold ≈ 8.5 min).
That cost is the real ceiling, not memory.

### Change-safety gate for §1 + §2 (apply-time, fleet-window op)

- **Precheck:** `GET /api/ps` on isis. Record `context_length`. `litellm --config ... --test` or a
  `yaml.safe_load` parse of the edited file must succeed.
- **Apply:** comment-preserving line edit (the wq#4213 pattern), then a LiteLLM reload.
- **Post-probe that can fail:** send a ~12,000-token prompt through LiteLLM to `lumen/code` with
  `max_tokens: 1`. Read `usage.prompt_tokens`, or Ollama's `prompt_eval_count` via `/api/chat`.
  **Pass** if it is ≥ 11,500 and `/api/ps` shows `context_length: 32768`. **Fail** at ≈4096: that
  means truncation, so the pin didn't take. Without the pin, this probe failed at ~07:47Z today, so
  it can fail.
- Roll back on failure: revert the commit and reload.

## 3. Benchmark plan: a local model with reliable function calling

**Goal:** choose a model, provider path and context such that an agent harness's tool calls succeed
unattended. Choosing the harness comes after this.

### Hypotheses to separate (each is cheap to test)

- **H1 — provider path.** `ollama/` in LiteLLM goes through Ollama's `/api/generate` path. There,
  tools are prompt-emulated (JSON-mode) rather than sent as native `tools`. That would explain "tool
  call printed as text" even though the model reports a `tools` capability. Test the same model three
  ways: `ollama/`, `ollama_chat/`, and `openai/` against `:11434/v1`.
- **H2 — thinking leakage.** `<think>` reached the content field. Test with thinking off (`think:
  false` / `/no_think` in the system prompt for Qwen3-family) against thinking on.
- **H3 — context truncation.** Run at a pinned 32768 against an unpinned window (covered by §2).
- **H4 — model.** Only after H1–H3: compare candidates.

### Candidates (all must fit isis RAM, CPU-only unless a GPU is attached)

The SearXNG-backed `fleet-search web` was down this session (all engines suspended, already logged),
so the list below is from local knowledge, not a fresh survey. The plan's first step is a prior-art
refresh (BFCL v3/v4 leaderboard, Ollama `tools`-tag models) once search is back.

- `qwen3.6:35b-a3b` (incumbent, after H1/H2 fixes)
- `qwen3-coder:30b-a3b` (MoE, agentic-coding tuned, ~3B active, so CPU-viable)
- `qwen2.5-coder:14b` / `isis-coder` (already on disk, dense, which makes it slower per token on CPU)
- one non-Qwen control with native tool support in Ollama (e.g. a Mistral/Devstral or Llama-3.x
  tools-tagged build that fits RAM)

### Suites (fixed, committed fixtures; N≥5 per cell, temperature as the model card recommends)

1. **Tool-call validity (unit).** 20 committed prompts, each requiring exactly one call to a declared
   tool with typed args, sent as raw OpenAI `tools` through LiteLLM. Metric: % responses with a
   parseable `tool_calls[0]` whose name and args validate against the JSON schema. Content must hold
   no `<think>` tags.
2. **Multi-step (integration).** The wq#4207 task (write `list_toml.py`, run it, complete) run through
   gptme `--tool-format tool` and one other harness (Cline or OpenHands per wq#4166). Pin the input
   dir to a committed fixture tree, not a live repo.
3. **Long-context tool call.** Suite 1 prompts, each preceded by 20k tokens of filler. This catches
   truncation and long-context format decay.

### Metrics and pass bar

Each harness run is scored by an **external post-condition**, never by the harness exit code (§4).
The post-condition: expected file exists, its output matches the golden, and the `complete` tool or
equivalent was called.

| Metric | Pass bar for "fworker-eligible" |
|---|---|
| Suite 1 valid-call rate | ≥ 95% |
| Suite 2 post-condition success | ≥ 4/5 |
| Suite 3 valid-call rate | ≥ 90% |
| `<think>` leakage in content | 0 |
| p50 wall for suite 2 | ≤ 5 min (single slot; record it, don't optimise it) |

Record LiteLLM SpendLogs tokens per run; wq#4207 did not capture them.

### Execution notes

- isis is single-slot and serves fleet traffic. Run in a window, and announce on the board so other
  workers expect `lumen/code` latency.
- A new model pull plus a Modelfile per candidate is an isis change, so it goes through the same gate.
  Remove losing candidates afterwards (disk).
- Output: `reports/wq<new>_local_toolcall_bench.md` with the per-cell table and raw logs path. Then
  repoint `lumen/code` (gated) only if a candidate clears every bar.

## 4. Rule: wrappers around LLM CLIs verify a post-condition, not the exit code

> **Any wrapper, runner, or harness that invokes an LLM CLI (gptme, `claude -p`, aider, Cline,
> OpenHands, codex, …) MUST decide success by checking an external post-condition the task
> defines: an artifact exists and validates, tests pass, a commit or PR exists, a ticket state
> changed. An exit code of 0 is not evidence of success. A wrapper that cannot name its
> post-condition must record the run as `unverified`, not `success`.**

Evidence: wq#4207 T3. gptme + `lumen/code` ran 899 s, executed 0 tools, wrote no file, printed "No
tools used after 2 confirmations. Exiting." and **exited 0**.

Where it already holds, and where it should be applied:
- The fworker sentinel (`TICKET_CLOSED | BLOCKED | CLAIM_ERROR`) plus the critic is a post-condition
  check. Keep it.
- Any new runner (gptme, Cline, a local-model fworker) must ship its post-condition check in the same
  PR as the runner.

Recording: this report, plus an ISSUE-LOG row (this ticket). Promotion to `instincts.yaml` is
proposal-only (Tier 0 #7). It is a candidate for Frank's minerva/dream review, not auto-edited.

## 5. Follow-up tickets this proposal implies

1. Gated apply of §1 + §2, LiteLLM side plus the committed Modelfile (fleet-window op, wq#4213
   pattern).
2. `OLLAMA_CONTEXT_LENGTH` drop-in on isis (window; restarts Ollama on isis).
3. §3 benchmark (research-synthesis), blocked on `fleet-search` coming back for the prior-art
   refresh.
