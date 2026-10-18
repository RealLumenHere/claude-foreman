# wq#4294 — EVALUATE: task-orchestrator — MCP server for persistent work tracking across sessions

**VERDICT: DO NOT ADOPT for orchestrator amnesia. It solves a different problem than
`claude_context.sh --core`, and the problem it does solve (a persistent work-item graph
with dependencies and claims) is already owned by `wq` fleet-wide. Installed and exercised
for real (no Docker available in this worker's sandbox — used the PyPI/`uvx` Python port
instead, which is itself one of the two install paths the ticket names).**

## 1. What's actually in the repo vs. the ticket's framing

`fleet-search web` (DeepSeek fallback — SearXNG's free engines were all rate-limited/CAPTCHA'd
this session) surfaced the ticket's "Python port" directly: **`task-orchestrator-py`** on
PyPI, a port of `jpicklyk/task-orchestrator` (Kotlin/JVM original). Installed it for real with
`python3 -m pip install --target /tmp/fworker-wq4294/pkgs task-orchestrator-py` (version
resolved: **1.5.1**) — no Docker needed, confirming the PyPI README's claim that
`uvx task-orchestrator-py` runs with no install step.

- Ticket says "14 MCP tools." The installed package's `server.py` registers **far more** —
  I counted ~40 `@mcp.tool`-decorated functions (`task_orchestrator/server.py:179-996`),
  including `claim`/`release`/`conflicts`/`agent_register`/`agent_heartbeat`/`agent_list`
  (multi-agent coordination primitives) and `manage_workspaces`/`manage_checkpoints`/
  `export_graph`/`import_graph`/`get_metrics` that aren't in the original 14-tool list. The
  Python port has grown well past the upstream Kotlin server's scope.
- Ticket says "Docker container with SQLite persistence." True for the original, but the
  Python port needs neither: it's a plain `pip`/`uvx` package writing to a SQLite file at
  `TASK_ORCHESTRATOR_DB` (default `~/.task-orchestrator/tasks.db`) — confirmed by running it
  with `TASK_ORCHESTRATOR_DB=/tmp/fworker-wq4294/testdb/tasks.db` and inspecting the resulting
  file. Docker was not available in this worker's sandbox (`docker ps` denied by the
  allowlist), so this path was the only way to test it live, and it worked without issue.

## 2. Ran it for real (not a desk review)

Wrote two throwaway scripts (`/tmp/fworker-wq4294/run_eval.py`, `run_eval2.py`, outside the
installed package dir per the untrusted-download handling rule) that import
`task_orchestrator.engine.*` directly — the same functions the MCP tool wrappers in
`server.py` call — and ran them with `python3 -I`:

- `trees.create_work_tree(root=..., children=[...], deps=[...])` created a real epic +
  2 child tasks + 1 `blocks` dependency edge in a fresh SQLite DB in one atomic call.
- `transitions.advance_item(item_id, "start")` on the dependent item **raised
  `ToolError(DEPENDENCY_UNSATISFIED, "Blocked by unfinished items: install")`** when its
  blocker hadn't completed, then succeeded once the blocker was advanced through
  `start` → `complete` — the dependency-gated workflow (`queue → work → review → done`)
  is real, not just documented (`task_orchestrator/engine/transitions.py:49-57`).
- `queries.get_context()` — the ticket's "session-resumable context" tool — returned a
  JSON dashboard: `{"counts": {...}, "active": [...], "blocked": [{..., "blocked_by": [...]}]}`.
  This is a **task/dependency-state snapshot**, not a knowledge/memory snapshot: it tells you
  what work exists and what's blocking it, nothing about *why* decisions were made, what
  rules apply, or what tools exist.
- `schemas.check_gate()` (`task_orchestrator/schemas.py:86`) confirmed the "note schemas"
  claim is real: role transitions can be gated on required notes being present, not just on
  dependency completion.

## 3. Does this solve orchestrator amnesia better than `claude_context.sh --core`?

No — it's not solving the same problem. Read `claude_context.sh --core`'s actual body (fleet
snapshot at `/home/lumen/foreman/_runs/wq1630/snapshots/20260920T204421Z-home_lumen_minerva_context_claude_context.sh:12-73`,
since `/home/lumen/minerva` itself isn't in this worker's `--add-dir` set). On `--core` it
assembles, in order: a registered-identity gate (wq#1127), `handoff verify` + `HANDOFF.md`
(narrative session handoff), `FLEET_TOOLS.md` (capability registry re-rendered from
`fleet_tools.yaml`), tiered `INSTINCTS` (accepted procedural rules from `instincts.yaml`),
a live-fleet-state staleness check, and finally `context_selector.py`'s semantic retrieval
over `claude_memory.md` for "session start core context." That is: **what tools exist, what
rules to follow, what the fleet's live state is, and which past learnings are relevant** —
institutional/procedural memory, assembled fresh every boot so two unregistered sessions
can't collide on it.

`task-orchestrator`'s `get_context` answers a narrower, different question: **what tickets
exist and what's blocking them.** That's the same question `wq` already answers fleet-wide,
including the exact same primitives this Python port adds beyond the original 14 tools —
`claim`/`release`/`conflicts` duplicate `wq`'s own lease+fence+idempotency work (PR#46,
ISSUE-LOG #4) almost tool-for-tool. Adopting task-orchestrator fleet-wide would mean running
two independent ticket/claim graphs with no reconciliation between them — a second source of
truth for "what's in flight," which is the exact class of bug PR#46 was built to prevent, not
a fix for amnesia.

A `get_next_item`/dependency-gate layer *inside* an individual agent's own private workflow
(e.g. a single multi-step build task's own sub-steps) is a plausible narrow use, but that's
not what the ticket asks and isn't worth a second persistence engine on its own.

## 4. Recommendation

- **Do not install/deploy task-orchestrator fleet-wide.** It does not replace or improve on
  `claude_context.sh --core`; it duplicates `wq`'s claim/lease system under a different name.
- No build ticket follows from this eval.

## 5. Evidence trail

- Install: `python3 -m pip install --target /tmp/fworker-wq4294/pkgs task-orchestrator-py`
  (version 1.5.1, clean install, unrelated pre-existing dependency-conflict warnings from
  other fleet packages already in the environment).
- Entry point: `task_orchestrator_py-1.5.1.dist-info/entry_points.txt` →
  `task-orchestrator-py = task_orchestrator.server:main`.
- Live gate-enforcement run output: `GATE ENFORCED (expected failure): DEPENDENCY_UNSATISFIED
  - Blocked by unfinished items: install` → `install completed` → `test item started
  successfully now that dependency is done` (this session, `run_eval2.py`).
- `claude_context.sh --core` body read from
  `/home/lumen/foreman/_runs/wq1630/snapshots/20260920T204421Z-home_lumen_minerva_context_claude_context.sh:12-73`.
