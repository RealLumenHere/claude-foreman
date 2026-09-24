# PILOT c9a5e4f476cc report — hierarchical memory patterns from the Always-On Agents survey

Runner: orch-burn-B-3f4634 (wq#2431), 2026-09-24. No recall run was made: the card's
precondition (current memory is a flat store) is false. Nothing was built or changed.

## What was measured

| check | result |
|---|---|
| semantic tier | `minerva.db` `state_facts` 1,469 rows, bi-temporal, with `fact_class` and `render_tier` columns |
| tier split | `render_tier` core 687 / topic 747 / null 35 |
| episodic tier | archival notes with `[archival/session]` and `[archival/code-ship]` kinds and salience scores, surfaced at every session boot |
| consolidation | `lumen_dream.py` `episodic_dupes_v2` (cosine 0.93), proposal-only with one exact-duplicate exception (wq#1638) |
| routing | `mem0/lumen_memory_router.py` routes to three tiers, per `minerva/v4/research/memory_service_design.md` |
| existing instrument | `eval/recall_golden.jsonl`, 257 rows (wq#572, wq#595) |

## Findings

1. The proposal's current state is out of date. It describes "a flat memory store" with dream and
   reflector consolidating over it. Lumen already separates episodic notes from semantic state
   facts, renders by tier, and consolidates the episodic side.
2. The two-tier structure the proposal asks for would be a third copy of the same split, in the same
   pgvector store, competing with `lumen_state` for the semantic role.
3. The deferral asked for a concrete pattern from the survey. None was named, and the card's
   "proposed change" is the structure that already ships.

## If someone still wants it

Name one survey pattern that current memory lacks (for example a specific promotion rule from
episodic to semantic), express it as a change to `lumen_dream.py`, and score it on
`eval/recall_golden.jsonl` under the wq#595 scoring semantics. That is a Memory V4 ticket, not a pilot.

RECOMMENDATION: SKIP — superseded: episodic + semantic tiers already ship (state_facts 1,469 rows, render_tier core 687 / topic 747, dream episodic dedup); no survey pattern named.
