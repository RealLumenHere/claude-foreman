# PILOT 656a58d65bd3 — Add graph-text hybrid memory layer for multi-hop QA

Experiment card. Generated 2026-09-25T20:34:39+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. The five spec sections below are authored by the generator, not
by the runner (wq#2690, `research/DEFER_PILOT_LANE.md` §3.1a): a runner that finds
them missing, or finds a DO-NOT-RUN banner, blocks the ticket rather than inventing
a measurement plan. Never edit the card during a run.

| | |
|---|---|
| Proposal | `656a58d65bd3` |
| Deferred | 2026-08-12 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/656a58d65bd3/REPORT.md` |

## Why this is a pilot and not a research prompt

Deferred for a measurement; pilot wq#2124, card /home/lumen/foreman/pilots/656a58d65bd3/CARD.md. Original: The technique is promising but requires significant effort and touches the retrieval pipeline; existing eval harnesses (DGM, pelican_bench) can validate impact, but we should first prototype on a subset to measure benefit before committing.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

Graph-augmented retrieval beats pgvector-only retrieval on multi-hop answer accuracy by at least 10 percentage points.

## Primary metric

Multi-hop answer accuracy, in percentage points (correct answers / total queries). Win is treatment accuracy >= baseline accuracy + 10pp on the pinned sample.

## Fixed sample

/home/lumen/foreman/pilots/656a58d65bd3/multihop_eval.jsonl, 200 multi-hop queries with gold answers (sha256 pinned in CARD.md).

## Baseline (measure FIRST)

```
python /home/lumen/foreman/pilots/656a58d65bd3/run_eval.py --queries /home/lumen/foreman/pilots/656a58d65bd3/multihop_eval.jsonl --retriever pgvector --rerank lumen-rerank --out /home/lumen/foreman/pilots/656a58d65bd3/baseline.jsonl
```

## Treatment

```
python /home/lumen/foreman/pilots/656a58d65bd3/build_graph.py --papers /home/lumen/foreman/pilots/656a58d65bd3/papers/ --db /home/lumen/foreman/pilots/656a58d65bd3/graph.sqlite
python /home/lumen/foreman/pilots/656a58d65bd3/run_eval.py --queries /home/lumen/foreman/pilots/656a58d65bd3/multihop_eval.jsonl --retriever pgvector+graph --graph /home/lumen/foreman/pilots/656a58d65bd3/graph.sqlite --rerank lumen-rerank --out /home/lumen/foreman/pilots/656a58d65bd3/treatment.jsonl
```

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

Lumen currently uses pgvector for research index embeddings (lumen-research-index.timer) and has a rerank service (lumen-rerank), but no graph-based memory for multi-hop reasoning.

## Proposal — proposed change

Implement a lightweight graph memory layer that extracts entities and relations from research papers and stores them in a graph database (e.g., NetworkX or a simple adjacency list in SQLite), then augment retrieval by traversing the graph to find multi-hop connections before reranking.

---

The report at `/home/lumen/foreman/pilots/656a58d65bd3/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
