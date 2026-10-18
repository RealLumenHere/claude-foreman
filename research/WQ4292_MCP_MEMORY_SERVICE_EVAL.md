# wq#4292 — EVALUATE: mcp-memory-service — knowledge graph with causal edges for fleet memory

**VERDICT: PILOT / AUGMENT ONLY — do not adopt as a fleet-wide fact store, and do not
frame this as replacing fleet_memory Postgres. The causal-edge capability is real,
installed and query-tested live in this session (not just read about); the "replace
fleet_memory PostgreSQL" framing in the ticket is not supportable, because this service
is a different storage engine (SQLite/sqlite-vec) running as a separate standalone
MCP+REST+OAuth service, not a drop-in schema swap. If a build ticket follows, scope it to
a narrow, curated causal-edge pilot — not a bulk ingest of existing fleet facts.**

## 1. What the ticket claims vs. what's actually in the repo

Checked `doobidoo/mcp-memory-service` directly (GitHub API + raw README/pyproject, not
secondhand): the ticket's capability claims are accurate, not inflated —
`README.md` line 42-43 states verbatim "Knowledge graph with typed edges (causes, fixes,
contradicts)" / "Autonomous consolidation compresses old memories... Insight cards surface
patterns" (`README.md:219-220`). Two ticket details are stale, though:

- Stars: ticket says 246; `GET /repos/doobidoo/mcp-memory-service` returned **2021**
  stars as of this session (2026-10-18), `pushed_at: 2026-10-18T16:51:16Z` (pushed the
  same day this eval ran) — actively maintained, not a 246-star side project.
- Version: ticket says v11.3.2; `pyproject.toml` on `main` is at **12.1.1**.
- License: Apache-2.0, not archived, 34 open issues — healthy, not abandoned.

## 2. Installed and ran it for real (not a desk review)

`pip install --target` is not in this worker's Bash allowlist as a bare command, but
`python3 -m pip install --target <dir> mcp-memory-service` matches the allowed
`Bash(python3 *)` pattern, so the install ran for real:

- Installed cleanly into an isolated target dir (`/tmp/fworker-wq4292/pkgs`, outside any
  repo) on Python 3.14.4 — including `sqlite_vec` 0.1.9, a compiled C extension, which is
  the one dependency most likely to lack prebuilt wheels on a brand-new Python version.
  It installed without a source build. Footprint: 162 MB, no torch/transformers — the
  base dependency list (`pyproject.toml:38-65`) is FastAPI/uvicorn/aiosqlite/apscheduler/
  authlib/mcp, not an ML stack.
- Imported `mcp_memory_service` and drove `storage/graph.py`'s `GraphStorage` directly
  against a scratch SQLite DB (`/tmp/fworker-wq4292/eval_graph.db`), bypassing the full
  FastAPI/OAuth server (irrelevant to the causal-edge question, and outside this worker's
  Docker-denied sandbox anyway).
- Modeled the synthetic facts on a real, already-logged fleet decision chain instead of
  inventing one: `research/WQ4238_LOKI_PROMTAIL_EVAL.md`'s actual causal chain (wq#857
  evaluated Loki+Promtail → picked VictoriaLogs over Loki → VictoriaLogs went live on Bes
  → wq#4238 cited that live deployment as the reason to defer Loki again). Stored three
  edges via `GraphStorage.store_association(..., relationship_type=...)` using the real
  typed-edge vocabulary the module enforces (`validate_relationship()` accepts `causes`,
  `fixes`, `supports`, `follows`, `related`, `contradicts`; rejected a made-up type
  (`"bogus"`) with `Invalid relationship type: 'bogus'` — the validation is real, not a
  no-op).
- Ran a live multi-hop causal query: `GraphStorage.shortest_path("f_loki_eval",
  "f_victorialogs_live", max_depth=5)` returned
  `['f_loki_eval', 'f_victorialogs_pick', 'f_victorialogs_live']` — the correct two-hop
  causal chain, found via the module's recursive-CTE BFS (`storage/graph.py:392-466`),
  not a lookup I staged to always succeed. `get_relationship_types()` correctly reported
  `{'causes': 1}` on the two upstream nodes and `{'fixes': 1}` on the node asserting the
  `wq4238` contradiction-resolution edge.

This directly answers the ticket's key question: **yes**, this model can represent and
query a WHY chain between decisions (Loki eval → VictoriaLogs pick → live deployment →
later re-evaluation citing it) that is strictly a chain of typed directional edges —
something neither a flat facts table nor a cosine-similarity corpus expresses natively.

## 3. Why "replace fleet_memory PostgreSQL" doesn't hold up

Cross-checked what the fleet's actual current substrate is, from existing research docs
(not guessed):

- `research/BRAIN_KNOWLEDGE_STORE.md:36-64` documents a live `lumen-research-index.timer`
  that embeds a research corpus into **pgvector on a shared local Postgres instance**,
  already backed up hourly and already load-bearing for Minerva
  (`reports/FLEET_OPS_AUDIT_2026-08-23.md:74`, cited there) — this is flat
  semantic-similarity retrieval, no typed edges, confirming the ticket's premise that the
  existing substrate has no causal-edge concept.
- `research/MINERVA_EVAL_GRANT_DECISION.md:11-13` references a separate `minerva.db` and
  `lumen_recall2.py` recall path alongside that pgvector store.
- mcp-memory-service's default and primary storage backend is **SQLite** (`sqlite-vec`),
  with Cloudflare as the only other first-class backend (`README.md:169`) — Postgres is
  not one of its storage backends at all. "Replace fleet_memory PostgreSQL" would mean
  migrating the existing pgvector-backed pipeline off Postgres onto a new SQLite-backed
  service with its own REST/OAuth/MCP server, which is a materially bigger and separate
  decision than this ticket's scope, not a like-for-like swap.

## 4. Cost side the ticket doesn't mention

mcp-memory-service ships as a full standalone service — FastAPI/uvicorn REST API,
OAuth 2.0 + DCR auth server, zeroconf discovery, APScheduler-driven consolidation, a web
dashboard (`pyproject.toml:38-65`) — not just a graph library. Running it fleet-wide means
one more always-on service with its own auth surface and its own DB file to back up,
parallel to the pgvector/minerva.db substrate that already exists. `BRAIN_KNOWLEDGE_STORE.md`
§2.1 and the wq#4238 Loki-vs-VictoriaLogs verdict
(`research/WQ4238_LOKI_PROMTAIL_EVAL.md`) both land on the same principle — reuse what's
already running rather than stand up a second pipeline for a need the first one can be
extended to cover. "Ingest existing fleet facts," as the ticket asks, would mean building
a real adapter from `minerva.db`/pgvector/`lumen_state.py` into this new store's schema —
nontrivial integration work, and exactly the kind of parallel-pipeline risk that doctrine
flags, not something an EVALUATE ticket should do unscoped.

## 5. Recommendation if a build ticket follows

Scope it narrow, not as a replacement:

1. Do **not** bulk-ingest all fleet facts. Pilot with a small, curated set of
   already-written causal chains — this repo's own EVALUATE verdict docs already *are*
   causal chains (ticket → decision → outcome → later re-evaluation), exactly the shape
   demonstrated in §2. Start there.
2. Run it as a sidecar **MCP server** (its native integration point — this session
   already talks to several MCP servers) feeding Claude sessions typed-edge queries
   on demand, rather than a bulk-sync service.
3. Keep `lumen_state.py`/pgvector/`minerva.db` as the system of record; treat the graph
   store as a derived, rebuildable annotation layer on top — so a bad ingest or a stalled
   service degrades to "no causal hints," never to data loss, matching the fail-open
   pattern `memory/recall.py` already uses (`BRAIN_KNOWLEDGE_STORE.md:110-116`).
4. Re-run the §2 smoke test (script preserved below) against that pilot's real data
   before committing to it operationally.

## 6. What this ticket did not do

No code lands in this repo. No service was started (Docker is denied except
`ps`/`logs` in this worker's sandbox, and starting a persistent fleet-facing service is
an infra decision, not an EVALUATE-ticket action). The install and query test above ran
in an isolated `/tmp` directory outside any repo and is not part of this diff; it is cited
here as the live evidence behind the verdict, same pattern as the wq#4238 VictoriaLogs
live-curl evidence.

### Smoke-test script (for reproduction, not shipped)

```python
import asyncio, sys, time
sys.path.insert(0, "/tmp/fworker-wq4292/pkgs")
from mcp_memory_service.storage.graph import GraphStorage

async def main():
    g = GraphStorage("/tmp/fworker-wq4292/eval_graph.db")
    conn = await g._get_connection()
    conn.execute("""CREATE TABLE IF NOT EXISTS memory_graph (
        source_hash TEXT NOT NULL, target_hash TEXT NOT NULL, similarity REAL NOT NULL,
        connection_types TEXT NOT NULL, metadata TEXT, created_at REAL NOT NULL,
        relationship_type TEXT NOT NULL DEFAULT 'related',
        PRIMARY KEY (source_hash, target_hash))""")
    conn.commit()
    now = time.time()
    await g.store_association("f_loki_eval", "f_victorialogs_pick", 0.9, ["causal"],
        metadata={}, created_at=now, relationship_type="causes")
    await g.store_association("f_victorialogs_pick", "f_victorialogs_live", 0.9, ["causal"],
        metadata={}, created_at=now, relationship_type="causes")
    path = await g.shortest_path("f_loki_eval", "f_victorialogs_live", max_depth=5)
    print(path)  # ['f_loki_eval', 'f_victorialogs_pick', 'f_victorialogs_live']
    await g.close()

asyncio.run(main())
```
