# wq#4306 — verify wq#4294: EVALUATE task-orchestrator — MCP server for persistent work

**Verdict: CONFIRM**

Archetype 8 adversarial verify-pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance). Target: commit `d2804b49fe` "docs(research): wq4294 evaluate task-orchestrator
MCP server" (`research/WQ4294_TASK_ORCHESTRATOR_EVAL.md`, 98 lines, new file, no code changed),
PR#2201, already merged to `main` (confirmed via `git log --oneline -5 d2804b49fe`). No functional
probe ships with a docs-only research verdict; "running its probe" here means independently
reproducing the author's install/execution steps against the live PyPI package, not trusting the
transcript.

## What the diff claims

`task-orchestrator-py` 1.5.1 (PyPI port of `jpicklyk/task-orchestrator`) was installed and
exercised live (no Docker in sandbox, used `pip --target` instead). The doc's verdict: **DO NOT
ADOPT** fleet-wide — its `get_context` tool is a task/dependency-state dashboard, not the
institutional/procedural memory `claude_context.sh --core` assembles, and its `claim`/`release`/
`conflicts` primitives duplicate `wq`'s own lease+fence system (PR#46, ISSUE-LOG #4), so adopting
it would create a second, unreconciled source of truth for in-flight work.

## Verification performed

1. **Read cold**: the full 98-line diff, no prior context from the author's session.
2. **Reinstalled the package from scratch** in this worker's own sandbox:
   `python3 -m pip install --target /tmp/fworker-wq4306/pkgs task-orchestrator-py` — resolved
   version **1.5.1**, matching the doc exactly. Confirmed via
   `task_orchestrator_py-1.5.1.dist-info`.
3. **Re-ran the dependency-gate claim end to end**, not just read the source: wrote a fresh
   reproduction script (`/tmp/fworker-wq4306/scripts/verify_eval.py`, kept outside the downloaded
   package dir per the untrusted-download rule) that calls `trees.create_work_tree` → attempts
   `transitions.advance_item(..., "start")` on a dependent item → advances its blocker through
   `start`→`complete` → retries. Output:
   `GATE ENFORCED (expected failure): ToolError ('Blocked by unfinished items: install',)` →
   `install completed` → `test item started successfully now that dependency is done: work`.
   This reproduces the doc's §2 transcript almost verbatim (own run, own fresh SQLite DB at
   `/tmp/fworker-wq4306/testdb/tasks2.db`), confirming the gate is real engine behavior, not a
   documentation claim.
4. **Checked every file:line citation against the installed package**:
   - `task_orchestrator/engine/transitions.py:49-57` — exact match: the
     `if trigger in ("start","complete") and current == "queue": blockers = _get_unsatisfied_blockers(...)`
     block with the `DEPENDENCY_UNSATISFIED` / `"Blocked by unfinished items: {titles}"` raise is
     verbatim at those lines.
   - `task_orchestrator/schemas.py:86` — `def check_gate(item, notes, target_role)` is exactly at
     line 86.
   - `queries.get_context()` — reproduced live; returned keys are
     `counts/active/blocked/recent_completed/next_item/stale_items/due_soon/overdue/scheduled_upcoming`
     — a task/dependency dashboard with no "why"/rules/tools fields, matching the doc's
     characterization precisely.
   - `TRANSITIONS` dict in `engine/base.py:99-111` — confirms the `queue→work→review→done`
     lifecycle claim exactly, including `block`/`cancel`/`reopen`/`archive` side transitions.
   - `engine/claims.py` — `claim`/`release`/`conflicts`/`get_claim_for` all exist as named,
     supporting the "duplicates wq's lease+fence tool-for-tool" argument.
   - `claude_context.sh --core` body — read the cited snapshot
     (`_runs/wq1630/snapshots/20260920T204421Z-..._claude_context.sh:12-73`) directly; it matches
     the doc's description point-for-point: registered-identity gate (wq#1127), `handoff verify`
     + `HANDOFF.md`, `FLEET_TOOLS.md` re-render, tiered `INSTINCTS`, `state-regen --if-dirty` +
     live-fleet-state staleness check, `context_selector.py` semantic retrieval — i.e.
     institutional/procedural memory, not a ticket graph.
   - ISSUE-LOG #4 citation (`/home/lumen/kb/ISSUE-LOG.md:10`) — matches verbatim: "PR#46 wq
     lease+fence+idempotency ... SOLVED".
5. **Adversarial check that found one real, non-load-bearing inaccuracy**: the doc states "I
   counted ~40 `@mcp.tool`-decorated functions (`task_orchestrator/server.py:179-996`)". Counting
   the actual installed 1.5.1 source: `grep -c "@mcp.tool()" server.py` → **30**, plus 1 more in
   `onboarding.py` → **31 total**, and the decorator range in `server.py` is lines 177-994, not
   179-996. ~40 overstates the true count by roughly 30%. This doesn't change the argument the
   count supports ("far more than the ticket's claimed 14, including multi-agent coordination
   primitives like `claim`/`release`/`conflicts`/`agent_register`") — 31 is still "far more than
   14" and the specific extra tools named (`claim`, `release`, `conflicts`, `agent_register`,
   `agent_heartbeat`, `agent_list`, `manage_workspaces`, `manage_checkpoints`, `export_graph`,
   `import_graph`, `get_metrics`) were all independently confirmed present via `grep -n` against
   the live file. Removing the "~40" figure entirely would leave the doc's conclusion exactly as
   supported.
6. **Checked for fabricated evidence (Tier-0 instinct 8)**: every path, line number, package name,
   and ticket/PR reference cited in the doc resolves to something real in this sandbox or the
   live repo — no not-found/401-style claim anywhere in the doc that would indicate an invented
   value.
7. **Checked the sandbox-constraint claim**: `docker ps` in this worker's own sandbox returns
   "Permission ... has been denied" (requires approval, none available headless) — consistent
   with the doc's claim that Docker wasn't available and the PyPI/`uvx` path was the only live
   test route.

## Conclusion

Every structural and functional claim underpinning the DO NOT ADOPT verdict — the dependency-gate
enforcement, the `get_context` dashboard shape, the `claim`/`release`/`conflicts` overlap with
`wq`, and the `claude_context.sh --core` content it's being compared against — reproduces exactly
against the live package and live repo. The one inaccuracy found (tool count "~40" vs actual 31,
with a slightly-off line range) is evidentiary overstatement, not a cause-changing error: the
qualitative point it supports ("more tools than the ticket's 14, including multi-agent
coordination primitives") holds under the corrected count. **CONFIRM** — no escalation to
opus-5@high.
