# wq#4296 — EVALUATE: multi-agent-hub — MCP dispatch hub with git worktree isolation

**VERDICT: DO NOT ADOPT — not as a replacement for foreman dispatch, and not as an
augment service either. The ticket's key question ("does MCP-native dispatch give
better isolation than our SSH-based fworker wrapper?") answers NO: the isolation
mechanism is literally the same one we already run (one git worktree + branch per
task), and the hub's version is *weaker-enforced* at every layer that matters. Two
or three of its implementation tricks are worth stealing as ideas; the project
itself is a 6-week-old, zero-star, single-author, Windows-first personal tool.**

## 1. What was actually evaluated (not a desk review of the README)

Cloned `zkylek1212-k/multi-agent-hub` at `a2cf64d` (2026-10-06, HEAD of main) into
`/tmp/fworker-wq4296/multi-agent-hub` and read the full source. Repo metadata via
GitHub API on 2026-10-18: **created 2026-09-02, 0 stars, 0 forks, 1 open issue,
last push 2026-10-06** — twelve days stale, one author, MIT license, no CI.

The entire system is:

- `mcp_worker_hub.py` (618 lines) — a FastMCP stdio server exposing 8 tools:
  `get_active_workers`, `git`, `delegate_to_worker`, `wait_for_job`, `list_jobs`,
  `check_job_status`, `run_in_sandbox`, `open_dashboard`.
- `dashboard_float.py` (348 lines) — a floating always-on-top progress window.
- `ARCHITECTURE.md` (723 lines, Traditional Chinese) — mostly a prose copy of the
  code plus a long "Robust System Prompts" section (§3) instructing the master LLM
  how to behave.
- `test_hub.py` (116 lines) — smoke tests, no CI running them.

Operating model: a "Master" agent (Claude Code / Claude Desktop / Cursor) loads the
hub as an MCP server. The master calls the generic `git` tool to create a worktree
per subtask, then `delegate_to_worker(prompt, worker_type, working_dir)` spawns a
CLI agent (claude / codex / agy / ollama) as a local async subprocess inside that
worktree (`mcp_worker_hub.py:269-305`), polls with sliced `wait_for_job`
(`mcp_worker_hub.py:309-333`), gates with a docker test sandbox
(`mcp_worker_hub.py:369-402`), and merges back with `git merge --no-ff`.

## 2. Key question: is MCP-native dispatch better isolation? No.

First, a framing correction: fworker dispatch is **not SSH-based**. SSH is how the
fleet reaches Lumen; fworker itself spawns headless `claude` CLI workers as local
subprocesses with a frozen per-worker settings file
(`foreman/harness/fworker.py:103` → `worker-settings.json`) in per-ticket worktrees
under `foreman/harness/fworker.py:118` (`WORKTREES_ROOT=/home/lumen/worktrees`).
So both systems are the same shape: local subprocess agents in git worktrees. The
comparison is purely about what each layer enforces.

| Layer | multi-agent-hub | fworker/foreman |
|---|---|---|
| Worktree creation | Master LLM calls generic `git` tool; hub never verifies | Harness creates/owns worktree deterministically |
| Working dir check | `delegate_to_worker` accepts **any existing directory** — only `Path(working_dir).is_dir()` (`mcp_worker_hub.py:244-246`). Nothing requires it to be a worktree, or inside the repo, or not `/` | Worktree path is harness-assigned, never LLM-chosen |
| Worker permissions | `claude --dangerously-skip-permissions`, full FS + network (`mcp_worker_hub.py:48-49`); codex gets `workspace-write` sandbox, others nothing | Per-command Bash allowlist, PreToolUse guard hook, filesystem bounded to worktree + explicit `--add-dir` |
| Process discipline | Orchestration SOP lives in a **system prompt** the master is asked to follow (`ARCHITECTURE.md` §3 exists precisely because masters skip steps) | Gates are code: hash-pinned acceptance.json, evidence replay by the harness, Stop hook refusing exit without evidence |
| Merge gate | Docker test sandbox (`--network none --memory 2g`, `mcp_worker_hub.py:392-398`) — good, but only tests run sandboxed; **the agent itself is unsandboxed** | Worker is the sandboxed thing; critic + evidence re-run gate the land |
| State | In-memory `jobs` dict (`mcp_worker_hub.py:109`) — hub restart loses everything; no queue, no retry, no circuit breaker | wq queue, redispatch, circuit breaker, prior-attempt notes |
| Git access | `git(args, repo)` is **arbitrary git in any repo path**, including push (`mcp_worker_hub.py:211-226`) | lumen-pr flow; direct-to-main and force-push denied by guard |

The "isolation" in multi-agent-hub is **merge isolation** (parallel workers can't
clobber each other's files — true of any worktree scheme, including ours since the
beginning). It is not security or permission isolation, which it actively
sacrifices (`--dangerously-skip-permissions` with network, no path bounds). MCP as
a transport contributes nothing to isolation; it only changes who the orchestrator
is — an LLM with tools instead of a deterministic Python harness. Our whole
evidence-gate design exists because LLM self-discipline-by-prompt fails; this
project's own 200-line "嚴禁跳過" (strictly forbidden to skip) system prompt is
an admission of the same failure mode, solved the weak way.

## 3. Could it augment (interactive fan-out from a Claude Code session)?

The one thing the hub offers that foreman doesn't is *conversational* dispatch: a
human-driven Claude Code session fanning out parallel CLI workers mid-conversation
with a live dashboard. But our harness already has the Agent tool + background
tasks for in-session fan-out, and anything heavier belongs in wq where it gets the
evidence gate. Standing up an unsandboxed second dispatch path alongside foreman
would be a governance regression, not an augment. No pilot recommended.

## 4. Ideas worth stealing (file them as candidate tickets, not adoption)

1. **Prompt handoff via file, fixed-ASCII command line** — the prompt is written to
   `.hub_prompt.md` inside the worktree and the CLI is invoked with one constant
   English sentence (`mcp_worker_hub.py:64-67,250-252`), sidestepping shell
   quoting, injection, and command-length limits on every worker type; the file is
   unlinked after the job so it never pollutes the diff (`mcp_worker_hub.py:296`).
2. **`--strict-mcp-config` as a recursion guard** — a worker running inside a repo
   worktree would otherwise load the project `.mcp.json`, acquire the hub's own
   tools, and recursively self-dispatch (`mcp_worker_hub.py:46-49`). Worth a
   one-off check that foreman workers can't do the moral equivalent through a repo
   `.mcp.json` in a worktree they're given.
3. **Two-phase docker test sandbox** — `network=True` for dependency install, then
   `network=False` for the test run, with `--memory`/`--cpus` caps
   (`mcp_worker_hub.py:376-398`). A clean pattern if we ever containerize
   acceptance-test replay.
4. **Sliced `wait_for_job`** — returns `[Still Running]` after `HUB_WAIT_SLICE`
   seconds instead of blocking the MCP call past client tool timeouts
   (`mcp_worker_hub.py:309-333`). Relevant to any long-poll MCP tool we build.

## 5. Bottom line

Same isolation primitive we already use, minus every enforcement layer we added on
top of it. Nothing here replaces foreman dispatch; the four patterns above are the
total salvage value.
