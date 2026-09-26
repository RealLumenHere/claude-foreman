# PILOT 2cfb144423c8 — Implement PI-Mem parallel-iterative memory module for long research documents

Experiment card. Generated 2026-09-25T20:34:34+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. The five spec sections below are authored by the generator, not
by the runner (wq#2690, `research/DEFER_PILOT_LANE.md` §3.1a): a runner that finds
them missing, or finds a DO-NOT-RUN banner, blocks the ticket rather than inventing
a measurement plan. Never edit the card during a run.

| | |
|---|---|
| Proposal | `2cfb144423c8` |
| Deferred | 2026-08-09 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/2cfb144423c8/REPORT.md` |

## Why this is a pilot and not a research prompt

Deferred for a measurement; pilot wq#1933, card /home/lumen/foreman/pilots/2cfb144423c8/CARD.md. Original: No experiment/eval harness exists to measure whether the technique actually improves Lumen's long-context reasoning, and the Memory v2 system is still in active iteration (P3). The paper's method is unproven for Lumen's specific workload and scale.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

PI-Mem iterative compression beats single-pass embedding on recall@5 of the correct source document for long research queries by at least 0.10 absolute.

## Primary metric

recall@5 — fraction of 40 held-out long-document queries whose correct source document appears in the top-5 retrieved chunks; win is treatment recall@5 >= baseline recall@5 + 0.10.

## Fixed sample

/home/lumen/foreman/pilots/2cfb144423c8/sample/ — 20 research documents each >32k tokens and 40 queries (2 per doc), pinned by sha256 manifest at /home/lumen/foreman/pilots/2cfb144423c8/sample/MANIFEST.sha256.

## Baseline (measure FIRST)

```
/home/lumen/foreman/pilots/2cfb144423c8/run_baseline.sh
```

## Treatment

```
/home/lumen/foreman/pilots/2cfb144423c8/run_pimem.sh
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

Lumen's archival memory relies on lumen-dream and lumen-reflector (Memory v2 P3), with research embeddings stored in pgvector via the lumen-research-index timer service. There is no explicit long-context iterative memory component.

## Proposal — proposed change

Create a new Python service (e.g., lumen-pi-mem) that implements the parallel-iterative memory algorithm from the PI-Mem paper. The service would process documents exceeding a configurable token threshold (e.g., >32k tokens) by iteratively compressing intermediate representations and storing them in pgvector, augmenting the existing research-index pipeline. It would be triggered by a systemd timer and run in a dry-run sandbox initially.

---

The report at `/home/lumen/foreman/pilots/2cfb144423c8/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
