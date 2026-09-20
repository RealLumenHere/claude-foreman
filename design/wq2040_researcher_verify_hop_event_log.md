# wq2040 — Fleet-researcher verify-hop + per-task event log (design)

_2026-09-20 · design ticket, mined from dsh patterns M1–M4 per `research/DSH_EVAL.md` §3.
Spec order: **M2 → M3 → M1 → M4**. Per the wq#2026 rule ("No researcher
prompt/playbook/graph change may ship until FRB-v1 exists"), **nothing in this doc
ships to the graph before FRB-v1** — with one carefully-scoped exception argued in §2.4.
This doc is the deliverable; the build tickets it specifies are listed in §7._

## 0. Scope and target

Target is the fleet researcher graph, `autonomy/lumen_fleet_graph.py` (LangGraph
`StateGraph`): `load → web_search → local_retrieval → call_llm → quality_gate →
verify_opt → write_output`, ~133 outputs/24 h, one LLM call per task (+retry), free-tier
providers via LiteLLM (`DSH_EVAL.md` §2, all [V] at eval time).

**Verification limit, stated up front:** this design lane's sandbox excludes
`/home/lumen/autonomy` (fleet-exec policy), so node names and call paths above are cited
from `research/DSH_EVAL.md` §2 [V] and `research/DEFER_PILOT_LANE.md` §3.3
(`write_output` is the implementation `lumen_fleet_researcher.worker → run_task`
delegates to), not re-read this session. **Every build ticket in §7 starts by
re-verifying node names and state shape against the live file.**

Non-goals: no dsh runtime, no Node dependency, no second harness (wq#2038 Option A —
reject runtime, mine patterns). No changes to scout/intel/signal pipelines (zero LLM
calls; nothing to gain — `DSH_EVAL.md` §2).

## 1. Ship order and the FRB circular dependency

The spec order M2 → M3 → M1 → M4 is kept, for the spec's reasons:

| Step | What | Why this position |
|---|---|---|
| M2 | per-task append-only event log | it is the substrate FRB-v1 §3.2 needs (snapshot + replay of `web_search`/`local_retrieval`). Build once, serve both. |
| M3 | spill policy on oversized tool text | prerequisite for M1: one fetched README can exceed the whole free-tier context. Deterministic, no model call. |
| M1 | `verify_hop` node | the behaviour change that must be measured by FRB before/after (fabricated-entity rate is FRB metric M1). |
| M4 | LangGraph checkpointer | fail-closed durability; lowest value while tasks are ~1 call. |

There is a circular dependency the spec leaves implicit: **FRB-v1 needs M2's log to
capture snapshots, and the wq#2026 rule blocks graph changes until FRB-v1 exists.**
§2.4 resolves it: M2 is designed as an observability-only wrapper with a demonstrated
no-behaviour-change guarantee, which satisfies the *intent* of the freeze rule (the rule
exists so unmeasured behaviour changes can't ship) while unblocking FRB capture. M3 and
M1 change model-visible text and are unconditionally FRB-gated. M4 does not change
model-visible behaviour but has no urgency; it ships last.

## 2. M2 — per-task append-only event log

### 2.1 Shape

One JSONL file per task: `autonomy/fleet_runs/<YYYY-MM>/<task_id>.jsonl`, append-only,
one line per event, flushed per line. Every model-visible fact is an ordered, durable
event; conversation state remains LangGraph's — the log is **derived observability plus
replay substrate, never authoritative** (dsh `core/session` pattern, inverted: dsh
derives history from the log; we only mirror into it, so a logging fault cannot corrupt
a run).

Event record:

```json
{"ts": "2026-09-20T04:11:02.113Z", "task_id": "…", "seq": 7,
 "node": "web_search", "phase": "exit", "elapsed_ms": 1240,
 "payload": {…full tool payload…}, "payload_sha256": "…", "spilled": false}
```

Phases: `enter` / `exit` / `error`. Payloads per node: `load` → prompt path + full
prompt text; `web_search` / `local_retrieval` → query + **full** results (spec M2: "full
tool payloads"); `call_llm` → request (model id, params, rendered messages) and raw
response; `quality_gate` / `verify_opt` → verdict + reasons; `write_output` → output
path, status, requeue reason if any.

### 2.2 Implementation: wrap at registration, not in node bodies

A `log_event()` helper plus one decorator applied where nodes are registered
(`add_node("x", logged(x_fn))`). Node bodies are untouched — this is what makes the
no-behaviour-change guarantee auditable: the diff to `lumen_fleet_graph.py` is the
decorator import and the registration lines, nothing inside a node.

### 2.3 Policies

- **Failure:** advisory in production — a log write error emits one Langfuse/stderr
  warning and the run continues. Under `FRB_CAPTURE=1` or `FRB_REPLAY=1` it is
  **fail-closed**: a run whose log is incomplete cannot be scored or replayed, so the
  task exits nonzero rather than produce an unscoreable artifact.
- **Masking (instinct 4):** before write, payloads pass the standard mask filter
  (env-shaped strings, `Authorization:` headers, key-shaped tokens). Researcher traffic
  is public-web + prompt files, so hits should be zero; the filter is there for the day
  that stops being true.
- **Retention:** at ~133 tasks/day with full payloads, budget estimate ~50–200 KB/task
  → single-digit GB/quarter. gzip files older than 7 days, purge at 90 (mirrors the
  vlogs-purge precedent, wq#1747). Purge never touches files referenced by a pinned FRB
  bundle — those are copied into the bundle at freeze, not referenced in place.

### 2.4 FRB tie-in and the pre-FRB shipping argument

The FRB snapshot for a task is a **filtered copy of this log** (the `web_search` and
`local_retrieval` exit events). `FRB_REPLAY=1` keys replay on `(node, sha256(query))` →
recorded payload; a query with no recorded key is a replay miss and fails the run
(fail-closed, consistent with FRB §3.4).

M2 may ship before FRB-v1 **only** with this evidence attached to its PR: an A/B run of
≥20 live tasks, wrapper on vs off, demonstrating byte-identical model-visible state and
outputs (diff of rendered messages and written `.md` notes). If the diff is not empty,
M2 is not observability-only and waits for FRB like everything else.

## 3. M3 — spill policy (bounded tool text)

Pure function, no model call:

```
spill(text, head=2000, tail=500) -> (preview, locator) | (text, None)  # if under budget
```

`preview` = head + `\n…[spilled]…\n` + tail. `locator` =
`[spilled: sha256=<h> bytes=<n> full text at fleet_runs/<YYYY-MM>/<task_id>.jsonl seq=<seq>]`
— the full text is already in the M2 event record, stored once; the spill wrapper puts
only the preview + locator into model-visible state. Applied at two choke points:
`fleet_search.search_context` results and the M1 fetcher (§4). Budgets are per-source
config, not code.

dsh's `compaction-tool-result-pruner` also rewrites *old* turns; we skip that — the
researcher graph is single-shot per task, so there is no long transcript to prune. If
M1's loop ever grows turns, pruning old previews to locators is the natural extension.

**FRB-gated:** truncation changes what the model sees. Ships behind FRB before/after.

## 4. M1 — `verify_hop` node

### 4.1 Placement and behaviour

New node between `quality_gate` and `write_output`, generalising the existing
arXiv-only `verify_opt`. **v1 is fully mechanical — zero LLM calls:**

1. **Extract** every checkable entity from the draft note: repo paths, URLs, arXiv IDs,
   package names, hostnames. The extractor is *the same one FRB metric M1 uses* —
   written once, shared, so the graph optimises exactly what the benchmark measures
   (which is the point: it closes the fabricated-entity class, it cannot game the
   metric because the metric's gold allowlist lives under the `frb` user, unreadable —
   FRB §3.4).
2. **Fetch** each entity's primary page once (GitHub API for repos, arXiv API for IDs,
   HEAD-then-GET for URLs, registry API for packages), through the M3 spill wrapper.
3. **Annotate:** resolves and matches → untouched. 404 / name-mismatch / redirect to a
   different entity → the citing sentence is marked inline `[unverified: <entity> —
   <reason>]`; an entity that is load-bearing for the RECOMMENDATION line demotes the
   verdict per the existing quality-gate contract. v1 **never blocks the write** —
   fail-open annotation; `quality_gate` remains the fail-closed gate.

### 4.2 Hard caps (dsh `guard/*` patterns, made non-negotiable)

- ≤ 2 hops per entity (page → one follow, e.g. repo → its README/redirect target).
- ≤ 8 fetches per task; entities past the cap are marked `[unverified: fetch budget]`.
- Wall-clock ≤ 120 s for the whole node; cooperative per-fetch timeout 15 s surfaced as
  a plain error string in the annotation, never an exception.
- Repeat-fetch guard: an identical URL is never fetched twice in a task (advisory nudge
  in dsh; a hard dedupe here, since no model is choosing the fetches in v1).

Call-volume: v1 adds **HTTP fetches only, zero LLM calls** — cheaper than the ~3×
model-call multiplier DSH_EVAL §2 budgeted for a 2-hop verify loop. A future v2 that
lets the model revise the note after annotations is a separate FRB-measured change.

### 4.3 Adoption gate

Ships only after FRB-v1, adopted only on FRB HOLDOUT before/after: fabricated-entity
rate (FRB M1) must drop; trap rate (FRB M4) must not rise — any rise disqualifies
regardless of other metrics (wq#595 rule, FRB §3.3).

## 5. M4 — checkpointer

`SqliteSaver` at graph compile (`autonomy/fleet_graph_checkpoints.sqlite`,
`thread_id = task_id`), checkpoint before `call_llm` and before `write_output`
(dsh `session-checkpoint-policy`: durable write before model request and before tool
side effect). An interrupted `write_output` recovers as **unknown outcome**: on resume,
check whether the note exists on disk before rewriting; never blind-retry the LLM call.
Checkpoint-write failure is fail-closed (skip the model call, requeue the task).
Lowest priority while tasks are ~1 call; ships after M1, or opportunistically with the
M2 A/B harness since it also has a no-model-visible-change profile.

## 6. Deployment

`autonomy/` is not a git repo (DEFER_PILOT_LANE §3.3). Modules
(`fleet_run_log.py`, `spill.py`, `verify_hop.py`) are developed and reviewed under
`foreman/pilots/wq2040/` via PR, then deployed by copy with timestamped `.bak` — the
fleet standard for non-repo paths. The `lumen_fleet_graph.py` edit itself is the
registration-line diff (§2.2) plus node insertions, each with its own `.bak`.

## 7. Build tickets (specs ready to file once wq#2038 records Option A)

wq#2038 is a `[do-not-claim]` orchestrator-decision ticket, still open; §5 Option A
("design doc + ~4 build tickets") is the recommendation this doc assumes. Ticket specs:

1. **[build] M2 fleet-researcher per-task event log** — §2 of this doc; acceptance:
   decorator-only graph diff, ≥20-task A/B with byte-identical outputs attached,
   mask filter on payload path, retention cron. No FRB dep *if and only if* the A/B
   diff is empty.
2. **[build] M3 spill wrapper for search_context + fetcher** — §3; deps: FRB-v1
   (wq#2026), ticket 1. Acceptance: FRB before/after attached, budgets in config.
3. **[build] M1 verify_hop node (mechanical v1)** — §4; deps: FRB-v1, tickets 1–2, and
   the shared entity extractor landing in the FRB build. Acceptance: FRB HOLDOUT
   before/after, M1 down, M4 flat, caps demonstrated in the event log.
4. **[build] M4 SqliteSaver checkpointer** — §5; deps: ticket 1. Acceptance:
   kill-a-worker-mid-task resume test, unknown-outcome path exercised.

## 8. Provenance

`research/DSH_EVAL.md` and `research/AGENTFORGE_EVAL.md` (FRB-v1 spec, §3) were written
2026-09-18 but never committed — they survived only as untracked files in stash
`9844d14` ("untracked files on main"), while already being cited by shipped artifacts
(`ops/wq2039-signal-engine-retire/RUNBOOK.md:6`, wq#2038/#2040 ticket text). This
branch commits both alongside this doc so the citations resolve. Logged as ISSUE-LOG
entry (spec-source-missing class).
