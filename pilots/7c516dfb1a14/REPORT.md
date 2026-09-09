# PILOT REPORT 7c516dfb1a14 — Agent-Centric Data Store for Archival Memory and Research Index

Ticket: wq#1568. Card: `CARD.md` (this directory). Ran on the wq1568 build-worker
node (hostname `lumen`), not `hathor` as the card's node field specified — see
"Honest provenance notes" below.

## What was measured

Hypothesis (pre-registered in CARD.md before any run): unifying archival-memory
and research-doc metadata into one agent-centric SQLite+JSON table removes the
app-level merge step today's split schema needs for a cross-kind "find by tag"
query, so the unified query's p50 latency would be **lower** than the
split-schema baseline's by >=15%.

Both arms ran against the same fixed 5,000-record synthetic sample
(`sample.json`, seed 1568, sha256 `98e5cf933c3321f2ab690e7faae3e6d244fa2dcf2dbc46fc87c03fce96201350`),
same 200-query randomized tag sequence (seed 424242), in-process SQLite,
3 repeat trials per arm.

- **Baseline** (`run_pilot.py:27-53` `setup_baseline`/`query_baseline`): two
  normalized tables, one per kind, each with an indexed tag-junction table
  (mirrors minerva_memory.py's per-kind archival storage and
  lumen-research-index's document-metadata table). Cross-kind lookup = two
  indexed joins + a Python merge-sort.
- **Treatment** (`run_pilot.py:56-75` `setup_treatment`/`query_treatment`): one
  table, tags as a JSON array column, one SQL query using `json_each` (no
  index) — the smallest build matching the proposal's "SQLite + JSON columns"
  shape.

## Results (p50 latency, ms, n=200 queries/trial, 3 trials)

| trial | baseline p50 | treatment p50 | treatment / baseline |
|---|---|---|---|
| 1 (`results.json`) | 0.734 | 3.188 | 4.34x |
| 2 (`results_t2.json`) | 0.766 | 3.152 | 4.12x |
| 3 (`results_t3.json`) | 0.729 | 2.582 | 3.54x |

Win condition (fixed before the run): treatment p50 <= 0.85 * baseline p50
(i.e. treatment needed to be faster). **Not met, in either direction, by a wide
margin.** The treatment was 3.5x-4.3x *slower* than baseline in every trial,
not faster. The unindexed `json_each` scan over the JSON tags column costs far
more than the Python-side merge it was meant to save — the hypothesis is
falsified.

Insert/build cost (`build_ms`, not the primary metric but a secondary
observation): treatment was consistently faster to build (~40-44ms vs
~54-60ms for baseline across trials) since it skips the tag-junction-table
inserts. That is a real advantage in write path but it is a secondary
observation noticed after the primary numbers were in, not the pre-registered
metric, so it is recorded here and not used to justify the verdict.

## Scope this pilot did NOT cover

- No real production data (see data-handling note below): the sample is
  synthetic, so absolute latencies do not transfer to production row counts
  or content sizes. The *relative* result (unindexed JSON scan loses to
  indexed join on this access pattern) is a general SQLite property, not an
  artifact of the synthetic shapes, and would only get worse at scale since
  the JSON scan is O(n) while the indexed join stays O(log n).
  Adding an index over `json_each(tags)` (e.g. a generated column + index, or
  a companion tag table — which is what the current split schema already
  has) would close the gap, but doing so reintroduces the exact per-kind
  tag-junction-table structure the proposal wanted to eliminate, undercutting
  the "just SQLite + JSON columns, one service" simplicity claim.
- The proposal also implies a new standing service
  (`minerva-agent-store.service`) replacing two existing components
  (minerva_memory.py's storage layer and lumen-research-index's pgvector
  writer/reader). This pilot did not measure operational cost (one service
  vs. two, migration risk, pgvector's actual semantic-search capability which
  a JSON-column store does not replace at all) — those are real proposal
  costs/benefits this pilot is silent on.
- Semantic/vector search itself (lumen-research-index's actual job) is not
  replicated by a JSON-column SQLite store at all; this pilot only tested the
  tag-lookup access pattern, which is the one place the proposal's shape and
  the current shape are directly comparable.

## Honest provenance notes

- **The card was generated with its Hypothesis / Primary metric / Fixed
  sample / Baseline / Treatment sections blank** (`minerva_defer_review`
  emitted the raw template, same as pilot 0eed2f5bdb92). Those sections were
  written by this runner before the scored run, under the card's own "edit it
  before the run" clause.
- **Node deviation**: card specifies `hathor`; this worker ran on its
  assigned build-worker node (hostname `lumen`) since that is where the
  ticket was dispatched and the pilot required no fleet-specific resource.
- **Data deviation**: the card's data-handling section calls for a
  purpose-built extract of real archival-memory/research-doc columns.
  `minerva.db` is never copied to a pilot node (standing rule) and this
  worker's sandboxed filesystem did not have read access to
  `minerva_memory.py` or the lumen-research-index source to confirm exact
  column shapes or pull a compliant extract, so a synthetic, seeded,
  documented sample was used instead (`build_sample.py`). This is called out
  as a limitation above, not hidden.

## RECOMMENDATION: SKIP

Measured: treatment (agent-centric SQLite+JSON, unindexed json_each tag scan)
p50 tag-lookup latency was 3.5x-4.3x slower than the baseline (today's
normalized-tables-plus-indexed-junction shape) across 3 trials of 200 queries
each on a fixed 5,000-record sample (baseline p50 0.73-0.77ms, treatment p50
2.58-3.19ms). The pre-registered win condition (treatment p50 <= 0.85x
baseline) was missed in the wrong direction by a wide, consistent margin. The
one real advantage found (faster inserts, ~30% lower build time) was not the
metric this pilot pre-registered and does not offset the read-path regression
for a store whose stated purpose includes agent-facing lookups. Closing the
gap requires re-adding an indexed tag structure, which erodes the proposal's
"one simple JSON-column store" premise. Not recommended for ADOPT or WATCH as
specified; if revisited, the proposal should specify an indexing strategy for
JSON columns before a re-pilot.
