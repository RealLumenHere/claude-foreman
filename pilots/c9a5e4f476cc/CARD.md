# PILOT c9a5e4f476cc — Adopt hierarchical memory patterns from Always-On Agents survey into Minerva

Experiment card. Generated 2026-09-22T09:12:17+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `c9a5e4f476cc` |
| Deferred | 2026-08-13 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/c9a5e4f476cc/REPORT.md` |

## Why this is a pilot and not a research prompt

The survey is a broad overview; specific patterns need extraction and validation against Lumen's existing memory architecture before implementation. Defer until a concrete pattern is identified and a pilot is designed.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

(Filled in by the runner (wq#2431) after a precondition probe; the card was generated blank. No arm was scored.)

Adding a two-tier episodic/semantic hierarchy to Minerva's pgvector memory improves recall over the current flat store. Precondition: the current store is flat, with no episodic/semantic split.

## Primary metric

Recall on the pinned golden set (`eval/recall_golden.jsonl`, 257 rows per wq#572), win >= +5 points over baseline. Not reached: the precondition fails, see Baseline.

## Fixed sample

None pinned: no recall run was made. The instrument that exists is `eval/recall_golden.jsonl` (wq#572/#595).

## Baseline (measure FIRST)

Precondition check, read-only:

```
minerva.db state_facts rows            1469   (semantic tier: bi-temporal current facts)
  render_tier  core 687 / topic 747 / null 35   (a tier column already ships)
lumen_state.py: fact_class + render_tier columns, fact_verifications table
lumen_dream.py: episodic dedup of archival notes (episodic_dupes_v2, cosine 0.93), proposal-only consolidation
recent-episode layer at boot: [archival/session], [archival/code-ship] entries with salience scores
minerva/v4/research/memory_service_design.md: mem0 router already routes to three tiers
```

The proposal's stated current state ("flat memory store") is false.

## Treatment

Not built. The proposal asks for a structure that already exists in three forms (episodic archival notes, semantic bi-temporal state facts, tiered rendering). A fourth layer would duplicate them.

## Kill criteria

<!-- When to stop early WITHOUT asking permission. Stopping on a kill criterion is a
successful pilot whose answer is SKIP or WATCH, not a failed one. -->

- Wall-clock exceeds 90 min -> stop, report what was measured.
- Baseline does not reproduce -> stop, report the baseline defect. Do not proceed.
- Treatment incomplete at budget -> report partial numbers, RECOMMENDATION: WATCH.

## Data handling

`/home/lumen/minerva` is not a broker read root (credentials are mirrored into its
context sections) and `minerva.db` is never copied to a pilot node. A pilot that
needs fleet data takes a purpose-built extract of only the columns it needs, written
on Lumen, and the extract is listed here.

## Scope limit (standing, not per-pilot)

A pilot NEVER wires anything into prod. An ADOPT verdict feeds minerva-factory PLAN
like any other accept and is built through the normal path, with its own review.

## Proposal — current state

Lumen uses Memory v2 P3 (lumen-dream, lumen-reflector) with pgvector embeddings and a flat memory store; Minerva proposals are generated and evaluated via minerva-proposal and minerva-eval.

## Proposal — proposed change

Review the survey for hierarchical memory patterns and implement a two-tier memory hierarchy (episodic vs. semantic) in the Minerva layer, likely by adding a new table or index in the existing pgvector store and updating the memory consolidation logic in lumen-dream or lumen-reflector.

---

The report at `/home/lumen/foreman/pilots/c9a5e4f476cc/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
