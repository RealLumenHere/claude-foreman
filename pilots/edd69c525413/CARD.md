# PILOT edd69c525413 — Separate memory roles (episodic/semantic/procedural) into distinct modules

Experiment card. Generated 2026-09-15T09:15:14+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `edd69c525413` |
| Deferred | 2026-08-09 |
| Impact / effort / risk | M / M / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/edd69c525413/REPORT.md` |

## Why this is a pilot and not a research prompt

Memory v2 P3 is still actively consolidating; without an eval harness or stable memory API we cannot measure whether role separation actually improves retrieval. Defer until the consolidation pipeline stabilizes and we can run a sandbox A/B test in db6.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Node substitution (disclosed, precedent: a7d04e5ebe7d / wq#1570)

`wq show 1935` -> `needs :` blank, so `fleet-exec grant` denies with
`grant:grant_missing_env` (`research/DEFER_PILOT_LANE.md:76`) regardless of
`fleet-exec-policy.yaml` already listing hathor in `allow.ssh`; there is
also no fleet-exec verb for arbitrary exec on hathor even with a grant.
Both arms run on Lumen instead, in-process, stdlib only. This pilot has no
wall-clock-sensitive metric (pure CPU token matching), so the substitution
does not bias the comparison.

## Hypothesis

Classifying each memory fact into episodic/semantic/procedural and
filtering retrieval to a query's target role reduces cross-role
contamination in the top-k result set versus today's role-blind
token-overlap retriever (`memory/cmt_retrieval.retrieve_single_trace`), with
no loss of recall on the gold fact.

## Primary metric

`contamination_rate` — fraction of queries whose top-k retrieved set
contains a fact from a role other than the query's target role. Win: the
treatment's rate is materially lower than baseline's with no drop in the
secondary `recall_at_gold` metric.

## Fixed sample

17 hand-built facts (5 episodic / 7 semantic / 5 procedural) x 6 queries,
each tagged with a target role and one gold fact id.
`pilots/edd69c525413/sample.json`, sha256
`e3934d7f2074c2281986fbd2eecee730c1ebb262419b4afcaa651a8b5378733c`. Both
arms run against this exact sample in one process, baseline first.

## Baseline (measure FIRST)

```
python3 pilots/edd69c525413/run_pilot.py   # baseline arm: role=None
```

## Treatment

```
python3 pilots/edd69c525413/run_pilot.py   # treatment arm: role=q["role"]
# implementation: memory/role_retrieval.py (classify_role, retrieve_with_role_filter)
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

Lumen consolidates memory via lumen-dream and lumen-reflector (Memory v2 P3), storing entries as one undifferentiated set; no explicit role separation.

## Proposal — proposed change

Create a `lumen-memory-roles` service: a Python script that classifies each new memory entry via LiteLLM (e.g., deepseek-v4-flash) into episodic/semantic/procedural, stores the role in a sidecar SQLite table (memory_roles.db), and modifies retrieval queries to apply an optional role filter. No changes to the existing memory schema.

---

The report at `/home/lumen/foreman/pilots/edd69c525413/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
