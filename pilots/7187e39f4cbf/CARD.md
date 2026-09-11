# PILOT 7187e39f4cbf — Adopt a lightweight multi-agent economic simulation for agent coordination

Experiment card. Generated 2026-09-10T09:14:15+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `7187e39f4cbf` |
| Deferred | 2026-07-29 |
| Impact / effort / risk | M / L / L |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/7187e39f4cbf/REPORT.md` |

## Why this is a pilot and not a research prompt

The proposal is high-level and lacks a concrete mechanism; Lumen has no experiment harness to measure whether an economy improves agent coordination. A design document and small-scale A/B test (e.g., comparing research output quality with/without economy on one fleet member) should precede adoption.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

A minimal token-economy allocation mechanism (a shared budget spent by a global
value/cost price signal across agents) captures more total task value than
today's purely task-driven routing (a fixed per-agent sub-budget spent in
arrival order, no cross-agent signaling), under the same fixed shared token
budget and task pool, by at least 15%. Falsifier: if the economy arm's value
captured is not >=1.15x the no-economy arm's, the coordination overhead of an
economy is not worth it at this scale and the answer is SKIP or WATCH, not
ADOPT.

## Primary metric

**Total task value captured** (sum of `value` over completed tasks, unitless
research-output-quality score) under a fixed shared token budget of 100,
reported also as % of the fractional-knapsack theoretical optimum for the
same sample. Win condition, fixed before the run: economy-arm value captured
>= 1.15 * no-economy-arm value captured.

## Fixed sample

60 synthetic "research action" tasks (`cost` in tokens 1-12, `value` a noisy
multiple of cost), generated deterministically by `build_sample.py` with
`random.seed(1683)`, each task pre-assigned round-robin to one of the 3 fleet
researchers named in the proposal (cerebras, mistral, openrouter) as its
"home agent". No fleet extract was used: this models the proposal's own
example dynamics (agents choosing among research actions under a budget
constraint), not a specific historical Lumen dataset — there is no existing
Lumen telemetry of "agent picks task A over task B under a budget" to extract,
since that signaling doesn't exist yet (that is the whole premise of the
proposal). Sample is pinned by `sample.json` + its sha256, written once by
`build_sample.py` and reused unmodified by both arms.
Sample sha256: `031c565c05e578476cd55e6c11adda48a8c23a84c71861202a8e649eca3bf9ef`.

## Baseline (measure FIRST)

Today's state per the proposal's own "current state" section: "Agent
interactions are purely task-driven; no token-based signaling or budget
allocation exists." Modeled as: the shared budget is split evenly up front
across the 3 agents (no negotiation), and each agent completes its own
home-assigned tasks in a fixed arrival order (task id order) until its own
sub-budget is exhausted, with no visibility into other agents' tasks or
prices.

```
python3 run_pilot.py --arm baseline --sample sample.json --out results_baseline.json
```

## Treatment

The smallest mechanism matching "agents query the economy service for budget
constraints before selecting research actions": one shared token pool, tasks
from all 3 agents ranked by value/cost ratio (the price signal an economy
provides), tokens spent top-ratio-first regardless of which agent owns the
task. This tests the coordination mechanism itself, not the full proposal
(no systemd service, no timer, no LLM-simulated dynamics, no reputation
persistence — those are infrastructure around the mechanism, not the
mechanism).

```
python3 run_pilot.py --arm treatment --sample sample.json --out results_treatment.json
```

## Kill criteria

- Wall-clock exceeds 90 min -> stop, report what was measured.
- Baseline does not reproduce -> stop, report the baseline defect. Do not proceed.
- Treatment incomplete at budget -> report partial numbers, RECOMMENDATION: WATCH.

## Data handling

`/home/lumen/minerva` is not a broker read root (credentials are mirrored into its
context sections) and `minerva.db` is never copied to a pilot node. A pilot that
needs fleet data takes a purpose-built extract of only the columns it needs, written
on Lumen, and the extract is listed here. This pilot used a synthetic seeded sample
(see Fixed sample above) rather than a fleet extract, since no fleet telemetry of
agent-task-selection-under-budget exists to extract.

## Scope limit (standing, not per-pilot)

A pilot NEVER wires anything into prod. An ADOPT verdict feeds minerva-factory PLAN
like any other accept and is built through the normal path, with its own review.

## Proposal — current state

Lumen's agents (e.g., fleet researchers, evaluator, planner, generator) are orchestrated via lumen-orchestrator, lumen-prompt-engine, and messenger without any economic or resource-trading layer. Agent interactions are purely task-driven; no token-based signaling or budget allocation exists.

## Proposal — proposed change

Create a new systemd service (lumen-agent-economy) that implements a minimal token economy (e.g., reputation tokens or compute credits) shared among agents. Agents query the economy service for budget constraints before selecting research actions. Initially limit to the fleet researchers (cerebras, mistral, openrouter) and the evaluator/planner duo. Use an existing lightweight model (e.g., lumen-lfm2-1b) to simulate economic dynamics periodically (e.g., once per hour via a timer). The service stores state in a new JSON file at /data/lumen/economy/state.json. No database changes.

---

The report at `/home/lumen/foreman/pilots/7187e39f4cbf/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
