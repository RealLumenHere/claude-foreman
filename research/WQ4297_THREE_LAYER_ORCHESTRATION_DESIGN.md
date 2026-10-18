# wq#4297 — DESIGN: three-layer orchestration architecture (rules / memory / execution)

**Thesis of the ticket:** orchestrator amnesia is caused by rules, memory, and execution
living in one undifferentiated space; the fix is to separate them into Layer 1 (rules —
compile + validate policy), Layer 2 (memory — structured, queryable, verifiable facts with
causal chains), Layer 3 (execution — isolated task dispatch), and to add the four things we
lack: contradiction detection, causal chains, scoped retrieval, outcome-aware deprecation.

**Verdict of this design:** the three-layer *separation* already exists in the fleet and is
load-bearing — the ticket's own "wq=execution, lumen_state=memory, HANDOFF=rules" mapping is
right in spirit but wrong in two of three specifics (corrected in §1). The real work is not a
re-architecture; it is closing the four named gaps **as additive, derived, fail-open layers on
top of the systems that already own each concern**, in the cheapest-falsifiable order. Every
"adopt / replace" question this plan touches was already settled by a live eval this month
(wq#4211/#4292/#4294/#4295/#4296); this doc is the integration plan that stitches those five
verdicts into one build sequence. No new persistence engine, no new dispatch path, no graph DB
deployment, no LLM-judge pipeline. Net new code is small and in every case guarded by the
proposal-only / probe-gate doctrine already in force.

## 0. Method and sources

Grounded in the five EVALUATE verdicts filed into this same `research/` tree this month, plus
the live subsystems they read:

- `research/WQ4295_RULEBRIDGE_RCG_EVAL.md` — rules / contradiction detection (DO NOT ADOPT).
- `research/WQ4292_MCP_MEMORY_SERVICE_EVAL.md` — memory / causal chains (PILOT, derived only).
- `research/WQ4211_EVOLVING_AGENTS_EVAL.md` — outcome-aware deprecation + scoped routing
  (adopt three patterns, no dependency).
- `research/WQ4294_TASK_ORCHESTRATOR_EVAL.md` — execution (DO NOT ADOPT; duplicates `wq`).
- `research/WQ4296_MULTI_AGENT_HUB_EVAL.md` — execution/isolation (DO NOT ADOPT; salvage 4
  patterns).

Subsystem behavior is taken from those evals' first-hand reads of the live tree, not re-guessed:
`claude_context.sh --core` assembly order (WQ4294 §3, citing the `wq#1630` snapshot of
`claude_context.sh:12-73`), `instincts.yaml` schema-2 `supersedes`/footer-retirement mechanism
(WQ4295 §2), the pgvector + `minerva.db` + `claude_memory.md` memory substrate
(WQ4292 §3), and `wq`'s claim/lease/fence/idempotency (WQ4294 §3, ISSUE-LOG #4 / PR#46).

## 1. The separation already exists — corrected mapping

| Layer | Ticket's label | What actually owns it today | Evidence |
|---|---|---|---|
| **1 — Rules** | "HANDOFF" | `instincts.yaml` (35 rules, schema 2, tiered, `supersedes:` lineage + footer retirement log, `consolidation-proposal-only` gate). `HANDOFF.md` is *narrative session handoff*, not the policy corpus; `handoff verify` gates staleness. Both are **assembled** at boot by `claude_context.sh --core`. | WQ4295 §2; WQ4294 §3 |
| **2 — Memory** | "lumen_state" | Split substrate: `lumen_state.py` = live fleet state (hosts/IPs/leases); `claude_memory.md` + pgvector (`lumen-research-index.timer`) = semantic corpus; `minerva.db`/`lumen_recall2.py` = recall; episode store = per-session outcome log. `lumen_state.py` alone is the smallest of these. | WQ4292 §3; WQ4294 §3 |
| **3 — Execution** | "wq" | `wq` queue (claim/lease/fence/idempotency, redispatch, circuit breaker, prior-attempt notes) + foreman/`fworker.py` (per-ticket worktree, frozen settings, allowlist, PreToolUse guard, hash-pinned `acceptance.json`, evidence replay, Stop hook). | WQ4294 §3; WQ4296 §2 |

So the amnesia root cause is not "no separation." It is that the **memory layer is the only one
with no feedback edges**: it stores facts but cannot say which facts contradict, which caused
which, which are in scope for *this* task, or which have been outcome-falsified and should
decay. Rules (Layer 1) already have lineage (`supersedes`) and a review gate; execution
(Layer 3) already has outcome capture (critic verdicts, `failure.json`, episode store). The four
gaps are all Layer-2 shaped, plus one Layer-1↔Layer-2 edge (deprecation). That reframes the
whole ticket from "re-architect three layers" to "add four edges to the memory layer and wire
two of them back to rules and execution."

## 2. The four gaps — build / adopt / replace

### Gap A — Contradiction detection (Layer 1, rules)

- **Replace:** nothing.
- **Adopt:** nothing. `research/WQ4295_RULEBRIDGE_RCG_EVAL.md` settled this live: RuleBridge
  parses tool-config formats we don't use; Rule Coherence Graph's cheap syntactic pass caps at
  recall 0.5 and its polarity heuristic returned **42 spurious hits on 35 real rules** (WQ4295
  §3), while the expensive embeddings+LLM-judge tier is a reasoning call per pair every run — not
  proportionate to a 35-rule, single-author, already-`supersedes`-governed corpus.
- **Build:** at most a `<20`-line dup-id (+ footer-aware dangling-ref) pre-commit check on
  `instincts.yaml`, from the linter already written at `research/rule_lint.py`. Ship only if
  drift is observed in practice; until then this gap is **closed by the existing
  `supersedes` field + `consolidation-proposal-only` review gate**, which is more governance
  than either off-the-shelf tool offers.

### Gap B — Causal chains (Layer 2, memory)

- **Replace:** nothing. `research/WQ4292_MCP_MEMORY_SERVICE_EVAL.md` §3 showed "replace
  fleet_memory Postgres" does not hold — mcp-memory-service is SQLite/sqlite-vec with no Postgres
  backend; swapping substrates is a bigger, separate decision. pgvector / `minerva.db` /
  `lumen_state.py` stay the **system of record**.
- **Adopt (narrow, derived):** the typed-edge graph pattern — `causes` / `fixes` / `contradicts`
  / `supports` / `follows` edges with multi-hop `shortest_path` — which WQ4292 §2 installed and
  query-tested live (a real two-hop Loki→VictoriaLogs chain returned correctly). Run it as a
  **sidecar MCP server feeding typed-edge queries on demand**, not a bulk-ingest service.
- **Build:** a thin adapter that emits edges from artifacts that **already are causal chains** —
  this repo's own EVALUATE verdicts (ticket → decision → outcome → later re-eval) and the
  `supersedes`/retirement entries. Seed the pilot from those, not a bulk dump. The graph is a
  **derived, rebuildable annotation layer**: if the adapter stalls or a sidecar dies, retrieval
  degrades to "no causal hints," never to data loss — the fail-open pattern `memory/recall.py`
  already uses (WQ4292 §5).

### Gap C — Scoped retrieval (Layer 2, memory)

- **Replace:** nothing. `context_selector.py` already does semantic retrieval over
  `claude_memory.md` at `--core` boot (WQ4294 §3).
- **Adopt:** the *routing-learned-from-outcomes* idea from `research/WQ4211_EVOLVING_AGENTS_EVAL.md`
  (D3 lane-move learning) and `research/DIFFICULTY_ROUTING.md` — retrieval scope is just another
  routing key.
- **Build:** a scope key on retrieval so a ticket pulls facts relevant to **its ticket-class /
  lane / repo**, not the undifferentiated global corpus. Concretely: tag memory entries with the
  ticket-class/lane they arose from (the episode store already carries this) and bias the selector
  toward in-scope entries, with global as fallback. This is the direct cure for "amnesia": the
  session isn't failing to *store*, it's being handed the wrong slice at boot. Smallest viable
  version is a filter parameter on the existing selector, not a new store.

### Gap D — Outcome-aware deprecation (Layer 2 ↔ Layer 1)

- **Replace:** nothing.
- **Adopt (patterns, no dependency):** from `research/WQ4211_EVOLVING_AGENTS_EVAL.md` §2–4:
  (1) EAT additive-priority candidate scoring (low success rate +20, high fail count +15,
  orphaned +10, unused +5) run over the **episode store**; (2) the director's **TEACHES
  gate** — a memory edit is kept only if it beats its own probes and doesn't regress a pinned
  suite, landing as a blameable/bisectable patch; (3) the **`leak()` rule** — any learned-lesson
  probe that quotes ≥8 words of its source episode is invalid (this is our own wq#2630
  "probe must be able to fail" doctrine, mechanized).
- **Build:** an **evolution-strategist pass** over the episode store that scores
  (ticket-class × fact / worker-profile) cells by failure/rework rate and emits the top-N as
  *proposed* deprecations/edits to `minerva/dream/instincts_candidates.yaml` — respecting
  `consolidation-proposal-only` (instinct 7) exactly as the director's human-gated Accept does.
  This is what makes memory *outcome-aware*: a fact whose citing tickets keep failing decays into
  a review candidate instead of silently misleading the next session.

### Layer 3 — Execution (no gap; hold the line)

Both execution-layer evals returned DO NOT ADOPT, for the same reason: the candidates either
duplicate what `wq`/foreman already enforce, or enforce it weaker.

- **task-orchestrator** (WQ4294): its `get_context` answers "what tickets exist / what's
  blocking," which `wq` already owns fleet-wide; its `claim`/`release`/`conflicts` duplicate
  `wq`'s lease+fence+idempotency (PR#46) almost tool-for-tool. Adopting it = a second source of
  truth for in-flight work — the exact bug PR#46 prevents. **Do not adopt.**
- **multi-agent-hub** (WQ4296): same isolation primitive we run (worktree + branch per task)
  minus every enforcement layer — LLM-chosen working dir, `--dangerously-skip-permissions` with
  network, orchestration-by-system-prompt. **Do not adopt.** Salvage four patterns only, as
  candidate tickets: file-based prompt handoff with fixed-ASCII invocation; `--strict-mcp-config`
  as a worker recursion guard; two-phase (network-on-install / network-off-test) docker sandbox;
  sliced long-poll that returns `[Still Running]` before client timeout (WQ4296 §4).

## 3. Integration plan — interfaces and sequence

**Invariant (all four builds):** Layer 2 additions are *derived and fail-open*. Systems of
record are unchanged: `instincts.yaml` (rules), pgvector/`minerva.db`/`lumen_state.py` (memory),
`wq`/foreman (execution). Nothing auto-edits authoritative memory — all learned output is a
proposal routed through `consolidation-proposal-only` (instinct 7). A failed/stalled addition
degrades to today's behavior, never to data loss or a blocked boot.

**Assembly point stays `claude_context.sh --core`.** It already unifies the three layers at
session start (identity gate → handoff → FLEET_TOOLS → instincts → live-state → semantic recall).
The four gaps are changes to *what recall returns* and *what feedback flows back*, not a new
assembler. This preserves the prompt-cache prefix discipline and the "assembled fresh every
boot" property that stops two sessions colliding (WQ4294 §3).

**Sequence (cheapest-falsifiable first):**

1. **Gap C (scoped retrieval)** — highest amnesia-leverage, smallest change (a scope filter on
   `context_selector.py`, data already in the episode store). Probe from *outside* its default
   scope (a ticket class with no stored facts must fall back to global, not return empty).
2. **Gap D (outcome-aware deprecation)** — the episode-store scoring pass, proposal-only output.
   Reuses Gap C's scope tags. Probe-gated per WQ4211's TEACHES + `leak()` rules.
3. **Gap B (causal chains)** — the sidecar-MCP pilot seeded from existing verdict docs. Depends
   on nothing above; can run in parallel but is lower amnesia-leverage than C/D, so later.
4. **Gap A (contradiction lint)** — ship the `<20`-line hook only if/when drift is observed.

**What this explicitly does not build:** a new queue/claim system, a second dispatch path, a
Neo4j/graph-DB deployment, an LLM-judge contradiction pipeline, or a bulk fact migration. Each
was evaluated live and rejected on cost-vs-fit this month.

## 4. First concrete ticket to file

Gap C, minimal: add a `scope=` (ticket-class/lane) filter to the memory selector that biases
retrieval toward in-scope entries with global fallback, tagged from the episode store's existing
ticket metadata. Falsifiable acceptance: a ticket in class X with stored class-X facts must
retrieve them ahead of equally-similar global facts, **and** a ticket in a class with zero
stored facts must still return the global set (the out-of-scope probe that can fail, per wq#2630).
Everything else in this plan is a follow-on ticket gated on that one landing.

## 5. Cross-refs

wq#4211/#4292/#4294/#4295/#4296 (the five evals synthesized here); instinct 7
(`consolidation-proposal-only`); wq#2630 (probe-must-be-able-to-fail); PR#46 / ISSUE-LOG #4 (`wq`
lease+fence); `research/ORCHESTRATOR_OPERABILITY.md` (session time-loss friction — distinct
scope, no overlap); `research/DIFFICULTY_ROUTING.md` (routing keys, relevant to Gap C).
