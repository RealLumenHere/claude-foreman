# PILOT REPORT a7d04e5ebe7d — Complementary Memory Traces for Multi-hop Retrieval

Run: 2026-09-13, headless BUILD worker (wq#1684, attempt 2), worktree
`wq1684-a3`.

## Card and node substitution (disclosed)

`pilots/a7d04e5ebe7d/CARD.md` did not exist at dispatch (this is a stricter
instance of `kb/ISSUE-LOG.md` issue #257's card-generator defect — attempt 1
most likely stalled on this same missing file, silently, since its worktree
diff was empty). Written this session from the ticket's own detail text,
under #257's "edit it before the run" clause; logged as a new ISSUE-LOG
entry (blocked from landing directly — see "Known friction" below).

Ticket says "Run on hathor"; hathor has been down since 2026-07-18
(`kb/ISSUE-LOG.md` issue #33, reconfirmed via `pilots/2c11123fab8b/REPORT.md`,
wq#1682). Both arms ran on Lumen instead, per the wq#1560/#257 precedent.
This pilot has no wall-clock-sensitive metric, so the substitution does not
change the comparison.

## What was built

Lumen's actual memory reader, `memory/recall.py`, does top-k-newest
injection with no query conditioning at all — there is no live retrieval
path this pilot could extract an eval sample from, which is exactly the
"no eval harness exists" gap the defer reason named. This pilot built the
smallest one:

- `memory/cmt_retrieval.py` — baseline `retrieve_single_trace` (query vs.
  verbatim-fact token overlap) and treatment `retrieve_complementary_traces`
  (hop 1 = baseline; hop 2 re-queries by entity-shaped tokens — IPv4s,
  CamelCase/ALLCAPS identifiers, `wq#N` ids, file-ish paths — extracted from
  hop 1's own results). Additive only; nothing outside this pilot imports it.
- `memory/tests/test_cmt_retrieval.py` — 6 unit tests, passing
  (`memory/cmt_retrieval.py:1-104`, `memory/tests/test_cmt_retrieval.py:1-42`).
  Full existing suite still green: `cd memory && python3 -m pytest tests/ -q`
  -> 37 passed (31 pre-existing + 6 new).
- `pilots/a7d04e5ebe7d/build_sample.py` — deterministic synthetic fixed
  sample, 41 facts (8 two-hop bridge pairs + 24 distractors) and 8 multi-hop
  questions, grounded in real statements from this repo's own docs/ISSUE-LOG
  so every gold label is hand-checkable.
- `pilots/a7d04e5ebe7d/run_pilot.py` — scores both arms against
  `sample.json` on `recall_at_bridge` (fraction of questions retrieving
  BOTH gold facts in top-4), baseline measured first.

## Baseline (measured)

```
$ python3 run_pilot.py
sample_sha256=eaa55b8a7ecf7c60a3d53fe5c293f137f6c8d8450519e188fa7858bbaf49a0f0
baseline recall_at_bridge = 0.500 (4/8)
```

Full per-question detail: `pilots/a7d04e5ebe7d/results.json` key
`baseline.per_question`. The 4 misses (`hathor-down`, `qwen36-node`,
`wq1107-loop`, `fleet-memory-split`) all share the same shape: the second
gold fact contains none of the question's words, only an entity (an IP, a
date, a path) that the first gold fact names — a single verbatim trace has
no way to reach it.

## Treatment (measured)

```
treatment recall_at_bridge = 0.750 (6/8)
delta = 0.250
```

Treatment fixed 2 of the 4 baseline misses (`hathor-down`, `qwen36-node`) —
both bridge on an IPv4 entity, which the entity-trace regex catches. It did
NOT fix `wq1107-loop` (bridge entity "the loop breaker") or
`fleet-memory-split` (bridge entity "the distilled store"): both bridges
are generic noun phrases, not the IP/identifier/ticket-id/path shapes the
entity trace extracts, so hop 2 never re-queries on them. This is an
honest limitation of the cheap entity extractor, not of the two-hop
mechanism — full detail: `pilots/a7d04e5ebe7d/results.json`
key `treatment.per_question`.

## Result vs. the card's win condition

Card set, before running: delta >= +0.20 -> mechanism confirmed. Measured
delta = +0.25 (8 questions; every question is all-or-nothing, so this is 2
of 8 questions flipping true — too small an n for a p-value, which is why
the card used a magnitude threshold instead, decided before the run).
Mechanism confirmed on this sample.

## RECOMMENDATION: WATCH

recall_at_bridge=0.500 (baseline) -> 0.750 (treatment), delta=+0.250 on 8
fixed multi-hop questions (41-fact synthetic corpus,
sha256=eaa55b8a7ecf7c60a3d53fe5c293f137f6c8d8450519e188fa7858bbaf49a0f0).
The two-hop complementary-trace mechanism is confirmed on this harness and
clears the card's own ADOPT-strength threshold (+0.20), but n=8 questions
on a synthetic, hand-built corpus is not enough to commit `recall.py`'s
production path to a rewrite — it only proves the mechanism is real, not
that it pays off on live fleet memory traffic, where query and fact
phrasing may not share the clean entity shapes this sample was built with
(the 2 remaining misses already show the entity extractor's coverage gap
on generic-phrase bridges). WATCH: re-run this harness against real
`FLEET_MEMORY_ROOT` facts and real recall queries once enough episodes
exist to build that sample honestly, before an ADOPT decision feeds PLAN.

## Known friction (report per rule, not a blocker to this pilot)

Appending the missing-CARD.md finding to `/home/lumen/kb/ISSUE-LOG.md` was
refused: `guard.sh: BLOCKED — Write/Edit inside PRIMARY tree /home/lumen/kb
denied (wq#1719/wq#900)`. `kb/` is not part of this worktree's own repo, so
there is no `lumen-pr`-landable path to it from here. Recorded in this
report instead; a lane with `kb` write access should promote it to
ISSUE-LOG as issue #372 (missing pilot card, stricter instance of #257).
