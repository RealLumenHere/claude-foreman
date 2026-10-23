# wq#4539 — DESIGN: one render, one truth — claude_memory.md as a pure render; memory-table collapse; memory-mcp retirement

Status: DESIGN. Phases below land as follow-up tickets; phase 0 (QW5) is
independent and can land first. Source audit:
`/home/lumen/scratch/research/memory-system-audit.md` (MW1, S1, QW4, QW5,
F4/F5/F6/F11, s11 Q1) — that path is outside the BUILD-lane read sandbox
(dispatch advisory 469a6b37 flagged it), so this design works from the audit
findings quoted in the ticket plus facts re-verified directly against the
repos and noted per-claim below.

## Problem

`claude_memory.md` has two truths feeding one file, and the wrong one wins:

1. `minerva/context/context_builder.py` (wq#3647) regenerates the whole file
   from PG `fleet_memory` via `os.replace()` — wiping hand prose every
   `--core` boot (that's wq#4537's fix) and rendering from a store that is
   **frozen**: since 2026-10-08 PG fleet_memory took 1 decision write and 0
   fact/device/episode writes (audit F4).
2. The *live* truth moved elsewhere and the render never followed:
   - **Roster**: `lumen_state.py current` state facts (minerva.db). The
     correct-by-construction pattern already exists in this repo —
     `prompt-forge/fleet_context.py:88-104` builds the roster from measured
     state facts precisely because "a model that recites the roster from
     memory gets IPs wrong and resurrects retired nodes"
     (`fleet_context.py:5-8`). Meanwhile PG `fleet_devices` is wrong for six
     nodes: khepri, nephthys, sekhmet, ptah, hathor, isis (audit F5).
   - **Episodes**: minerva.db `archival_notes`, written by
     `memory/episodic.py` → `lumen_episode.py` → memoryd (wq#1635; see
     `memory/episodic.py:4-13`). PG `fleet_episodes` gets nothing.
   - **Decisions**: PG `fleet_decisions` is the only table still nominally
     authoritative, and two of its rows are actively wrong:
     - **#1 "NO API key"** contradicts ORCHESTRATOR-HANDOFF s4 (wq#4490).
     - **#14 "never reboot"** contradicts the tier-0 instinct set, which
       retired the never-reboot-without-permission rule (Thor-only now).
3. `memory-mcp.service` (loopback listener 127.0.0.1:3104 — see
   `ops/wq2751-listener-audit/test_wq2751.py:186-188`): 0 journal lines in
   7 days, no live code callers (audit F11; re-verified — the only repo hits
   are reports, the wq#2751 listener audit, and the wq#2768 hardening
   drop-in `ops/wq2768-access-guard/memory-mcp.service.d/`, none of which
   *call* it).

## Correction to the ticket's scope (new finding)

"Collapse PG fleet_memory into minerva.db" **cannot mean the database** —
only the four memory tables. The same PG database (`fleet_memory`, docker
lumen-pgvector, 127.0.0.1:5433) hosts the **`recipes` table with live
callers**: `harness/recipe_store.py` (wq#4235/wq#4288), imported by
`harness/fworker.py:55` and `dispatcher/foreman_dispatcher.py:92`, with
`PG_DBNAME = "fleet_memory"` at `harness/recipe_store.py:67`. The audit's
"frozen since 2026-10-08" is true of the memory tables and does not license
dropping the DB or the container. Scope everywhere below is:
**memory tables** = `fleet_facts`, `fleet_devices`, `fleet_episodes`,
`fleet_decisions`. The `recipes` table and the PG container stay; after
phase 2 the container's only remaining role is the recipe store.

## Design principle

`claude_memory.md` becomes a **pure render artifact**: every section has
exactly one authoritative store, the renderer reads only authoritative
stores, and nothing authoritative lives only in the rendered file (the one
exception, hand prose, gets a preserved region — wq#4537, a dependency of
phase 1). Instinct 7 is untouched: the render pipeline is the existing
boot-time regeneration, not memory consolidation; candidate facts still go
through minerva/dream for Frank review.

| claude_memory.md section | Authority (after this design) |
|---|---|
| roster / nodes            | minerva.db state facts (`lumen_state.py current`) |
| episodes / recent work    | minerva.db `archival_notes` (memoryd-gated) |
| decisions                 | PG `fleet_decisions` (phase 1, interim) → minerva.db `fleet_decisions` (phase 2) |
| hand prose                | preserved `hand-prose:begin/end` region (wq#4537) |

## Phases

### Phase 0 — QW5: supersede fleet_decisions #1 and #14 (lands first, no deps)

Append-only correction via `fm_db.log_event` (minerva/memory/fm_db.py —
confirm exact signature there; that repo is outside this lane's sandbox):
log a superseding event for #1 citing ORCHESTRATOR-HANDOFF s4 / wq#4490, and
for #14 citing the tier-0 instinct change. Do **not** delete rows — the
store's history stays replayable; the renderer and any reader must treat a
superseded decision as non-current.

Probe (can fail): after landing, render decisions; #1 and #14 show
superseded-by text; decision **#2 — an input from outside the changed set —
must still render as current**. If #2 also shows superseded, the supersede
logic over-matched and the probe fails.

### Phase 1 — one render: context_builder.py reads live stores

Rewire `minerva/context/context_builder.py` section by section:

- **Roster** ← minerva.db state facts, same subject/predicate parse as
  `prompt-forge/fleet_context.py:41-44,88-104` (reuse, don't reimplement
  parsing twice — ideally context_builder imports a shared helper or shells
  `lumen_state.py current` the way fleet_context does). This single change
  fixes all six stale-node rows, because retired/renamed nodes simply have
  no current `.role` fact.
- **Episodes** ← minerva.db `archival_notes` (most-recent-N digest lines).
- **Decisions** ← still PG `fleet_decisions` for now, but filtered to
  non-superseded rows (phase 0's contract).
- **Hand prose** ← preserved region from wq#4537 (hard dependency: landing
  phase 1 without wq#4537 keeps wiping prose on every regen).

Probe (can fail): regenerate twice; (a) rendered roster node set ==
`lumen_state.py current` node set, byte-stable across the two runs; (b) a
node present in PG `fleet_devices` but absent from current state facts
(any of the six stale nodes, e.g. a retired Mac — audit F5/wq#4541) must
**not** appear in the render. (b) is the outside-the-default input: it fails
if the renderer still reads PG for roster.

### Phase 2 — one truth: migrate decisions into minerva.db, freeze PG memory tables

1. `pg_dump -Fc fleet_memory` to the standard backup dir first (the
   `recipe_store.py:405` convention: dump before touching schema).
2. Create `fleet_decisions` in minerva.db mirroring the PG columns plus
   `superseded_by`/`superseded_ts`; copy all rows including phase 0's
   supersede events. Writes go through memoryd (audited), same trust gate as
   episode commits — no new unaudited writer.
3. Point context_builder's decisions section at minerva.db.
4. Freeze the four PG memory tables: revoke INSERT/UPDATE (or rename to
   `retired_*`) — do not drop; the dump plus the live tables are the
   rollback. `recipes` untouched.

Probe (can fail): with the PG memory tables revoked/renamed, (a) full render
succeeds and decisions match the migrated rows; (b) `pytest
harness/tests/test_recipe_store.py` still passes — the outside-scope input
proving the collapse didn't overreach into the recipes path.

### Phase 3 — retire memory-mcp.service (0 callers 7d)

Ops-lane runbook (systemctl disable is on the worker deny-list by design):

1. Re-check callers at execution time: journal lines over the trailing 7
   days and a fleet grep for `:3104` / `memory-mcp` outside reports and this
   design. The 0-callers evidence is from 2026-10-23; re-verify, don't trust.
2. `systemctl disable --now memory-mcp` via detached systemd-run (instinct
   5 pattern), keep the unit file in place for one week.
3. Remove the wq#2768 drop-in
   (`/etc/systemd/system/memory-mcp.service.d/30-wq2768-access-guard.conf`,
   staged copy at `ops/wq2768-access-guard/`), and update
   `ops/wq2751-listener-audit/test_wq2751.py:186-188` — the ":3104 still on
   127.0.0.1" check inverts to ":3104 refused" (a listener audit asserting a
   retired port is open would page forever).
4. Remove the `fleet-memory` MCP server entry from session configs after
   phase 2 (its `execute_sql` targets the PG memory tables; post-collapse it
   serves nothing a session should touch — recipe access stays code-only via
   recipe_store).

Probe (can fail): port 3104 refused on 127.0.0.1 **and** on the tailnet IP;
updated test_wq2751.py passes; one week of 0 journal lines before unit-file
removal. If anything starts logging to the unit in that week, the retirement
rolls back by re-enabling.

## Ordering and dependencies

```
QW5/phase 0 ──────────────┐
wq#4537 (prose region) ───┤→ phase 1 (render rewire) → phase 2 (collapse) → phase 3 (retire mcp)
wq#4538 (memoryd I-7) ────┘   (4538 unblocks episode freshness, not a hard gate)
```

## Open questions (for Frank — recorded, not gating; instinct 3)

- **Q1 (audit s11)**: carried as a pointer — the report section is outside
  this lane's sandbox. The orchestrator should surface it when sequencing
  phase 2.
- **Recipes long-term**: this design keeps PG as the recipe store. If Frank
  wants zero PG, migrating recipes to sqlite is a separate ticket reversing
  a deliberate recent choice (wq#4235/wq#4288) — not bundled here.

## Evidence ledger (claims → where verified)

| Claim | Source |
|---|---|
| roster-from-measurement pattern | `prompt-forge/fleet_context.py:5-8,88-104` |
| episodes land in archival_notes via memoryd | `memory/episodic.py:4-13` |
| recipes table live callers in fleet_memory DB | `harness/recipe_store.py:12,67`; `harness/fworker.py:55`; `dispatcher/foreman_dispatcher.py:92` |
| pg_dump-before-schema-touch convention | `harness/recipe_store.py:405` |
| memory-mcp loopback :3104 + audit check to invert | `ops/wq2751-listener-audit/test_wq2751.py:186-188` |
| wq#2768 drop-in to remove at retirement | `ops/wq2768-access-guard/apply.sh:19-25` |
| PG freeze dates, stale fleet_devices rows, decision #1/#14 contradictions, memory-mcp 0 journal lines | ticket wq#4539 quoting audit F4/F5/F6/F11 (report path sandbox-blocked this lane) |
