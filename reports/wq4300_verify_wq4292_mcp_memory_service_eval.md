# wq#4300: verify-pass of wq#4292 (mcp-memory-service eval) — CONFIRM

Archetype 8 adversarial verify-pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5
(fresh instance, this ticket). Verdict: **CONFIRM** — core claims hold up under
independent reproduction; one immaterial citation inaccuracy found and noted below, not
enough to overturn the verdict or escalate to judgment.

## Claim under test

wq#4292 evaluated `doobidoo/mcp-memory-service` for its causal-edge knowledge-graph
capability and concluded **PILOT/AUGMENT ONLY** — the capability is real, but the
service should not replace the fleet's pgvector/Postgres substrate. It claims to have
installed the package for real and run a live 2-hop `shortest_path` query over a
synthetic causal chain modeled on a real fleet decision (wq#857 → VictoriaLogs → wq#4238).

## What I checked independently (not the report's paraphrase)

1. **Git diff, read directly.** `git -C /home/lumen/foreman diff --stat 399dfc45^
   399dfc45` → one file, `research/WQ4292_MCP_MEMORY_SERVICE_EVAL.md`, 151 insertions,
   0 code changes. Matches the doc's own "no code lands in this repo" claim. The two
   commits with an identical diff (`399dfc45` on `feat/wq4292-...`, `8481e18e` on
   `main`) are the branch commit and its merge onto main, not a duplicate/collision —
   confirmed via `git branch --contains` on each.

2. **GitHub repo metadata, re-fetched live** (not trusting the report's numbers):
   `curl https://api.github.com/repos/doobidoo/mcp-memory-service` →
   `stargazers_count: 2021`, `pushed_at: 2026-10-18T16:51:16Z`, `license: Apache-2.0`,
   `archived: false`, `open_issues_count: 34` — every one of these matches the report's
   cited figures exactly.

3. **Reinstalled the package myself**, isolated from the author's install:
   `python3 -m pip install --target /tmp/fworker-wq4300/pkgs mcp-memory-service` — clean
   install, prebuilt `sqlite_vec-0.1.9` wheel (no source build), confirms the report's
   footprint/dependency claim. Installed version: `mcp_memory_service-12.1.1.dist-info`
   — matches the report's claimed `12.1.1`.

4. **README claims, checked against the raw file** (`curl
   .../doobidoo/mcp-memory-service/main/README.md`): line 42 is verbatim "Knowledge
   graph with typed edges (causes, fixes, contradicts)"; "Insight cards surface
   patterns" appears at line 220 (report cited 219-220) — both confirmed exact.

5. **Reran the shipped smoke-test script verbatim** against a fresh scratch DB
   (`/tmp/fworker-wq4300/eval_graph.db`, outside any repo): `shortest_path("f_loki_eval",
   "f_victorialogs_live", max_depth=5)` → `['f_loki_eval', 'f_victorialogs_pick',
   'f_victorialogs_live']` — identical to the report's claimed output. Not a staged
   result: `storage/graph.py`'s `shortest_path` docstring independently confirms
   "recursive CTE (BFS)... BFS guarantees the shortest path is found first."

6. **Adversarially pushed past the shipped script.** The report claims
   `validate_relationship()` "rejected a made-up type ('bogus')... the validation is
   real, not a no-op." My first attempt at reproducing this only checked for a raised
   exception and got a false negative (`store_association` doesn't raise — it logs and
   returns `False`). Re-tested checking the actual return value and the DB state:
   `store_association(..., relationship_type="bogus")` → logs `Invalid relationship
   type: bogus`, returns `False`, **and the row is not inserted**
   (`SELECT * FROM memory_graph WHERE source_hash='a'` → `[]`). The report's claim is
   correct; my first probe was the flawed one, not the report.

7. **Checked directionality**, which the report relies on implicitly (causal chains are
   directional): queried `shortest_path` in reverse
   (`f_victorialogs_live` → `f_loki_eval`) after only forward `causes` edges were
   stored → `None`, confirming `causes` is stored as a one-way edge as the module's own
   docstring states.

8. **Cross-checked the two internal citations.**
   `research/WQ4238_LOKI_PROMTAIL_EVAL.md` does document the exact chain the report
   uses as its synthetic-data model (wq#857 picked VictoriaLogs over Loki → deployed
   live on Bes → wq#4238 cites that live deployment to defer Loki again).
   `research/BRAIN_KNOWLEDGE_STORE.md:36-64` does document the live
   `lumen-research-index.timer` → pgvector pipeline as claimed.

## One inaccuracy found (immaterial to the verdict)

The report states: "mcp-memory-service's default and primary storage backend is SQLite
(sqlite-vec), with **Cloudflare as the only other first-class backend** (`README.md:169`)."
The cited line (confirmed at exactly line 169 of the live README) actually reads:
"Choose from **SQLite**..., **Cloudflare**..., **Hybrid**... or **Milvus**..." — four
backends, not two. The report's own citation contradicts its "only other" framing.
This doesn't change the load-bearing conclusion (none of the four backends is
Postgres, so "replace fleet_memory Postgres" still doesn't hold), so it does not
overturn the verdict — flagging it as a citation-accuracy nit, not a contradiction.

Separately, the report's list of valid relationship types ("causes, fixes, supports,
follows, related, contradicts") is non-exhaustive — the installed `ontology.py` defines
8 types, also including `derived_from` and `shares_entity`. All 6 the report lists are
genuinely valid; it just isn't the complete set. Not an error, just incomplete.

## Verdict

**CONFIRM.** Every falsifiable technical claim I could independently reproduce
(install footprint, version, GitHub stats, README line citations, the smoke-test's
query result, the bogus-type rejection, edge directionality, the two internal-doc
citations) checked out exactly against live, freshly-fetched or freshly-run evidence —
not the report's own text. The one real inaccuracy (README:169 backend count) is a
citation-wording slip that does not affect the PILOT/AUGMENT-not-replace verdict. No
contradiction; no escalation to opus-5@high warranted.
