# PILOT 0d51b20dba5f — Implement reward machines for STL-based task specifications in research pipelines

Experiment card. Generated 2026-09-24T09:15:00+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `0d51b20dba5f` |
| Deferred | 2026-08-18 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/0d51b20dba5f/REPORT.md` |

## Why this is a pilot and not a research prompt

The theoretical framework is promising but requires a concrete integration design and evaluation against existing harnesses (e.g., DGM v2) to determine if it improves research-to-action conversion; defer until a specific pilot is defined.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

(Filled in by the runner (wq#2445) after a precondition probe; the card was generated blank. No arm was scored.)

Guiding a research pipeline with reward machines built from Signal Temporal Logic specs improves research-to-action conversion over the current heuristic planning. Precondition: a live research orchestrator exists to integrate with.

## Primary metric

Research-to-action conversion rate (accepted actions per completed research task) on a pinned set of past runs, win >= +10% relative. Not reached: the precondition fails, see Baseline.

## Fixed sample

None pinned: no run was scored.

## Baseline (measure FIRST)

Precondition check, read-only:

```
systemctl list-unit-files 'lumen-orchestrator*'         0 unit files installed
/etc/systemd/system/DISABLED-research-mill.bak/lumen-orchestrator.service   parked, file dated 2026-08-23
/home/lumen/agent/logs/orchestrator.log                 last entry 2026-08-08 07:09 "Orchestrator complete: 0 prompts, 0 high-value"
lumen_deep_research_orchestrator.py                     last modified 2026-07-11
fleet notes: research mill retired 2026-08-28
lumen-prompt-engine                                     no unit; prompt_engine.py is a module
existing STL / reward-machine code                      none found under agent/ or minerva/ source
```

The proposal's integration target is retired.

## Treatment

Not built. The proposed service `lumen-stl-reward` would attach to `lumen-orchestrator`, which has not run since 2026-08-08 and is parked with the retired research mill.

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

Lumen's autonomous research pipelines (e.g., lumen-orchestrator, lumen-prompt-engine) use heuristic or LLM-based planning without formal task specifications; no existing service encodes Signal Temporal Logic (STL) constraints.

## Proposal — proposed change

Add a lightweight Python library (e.g., 'reward_machines') and a new service 'lumen-stl-reward' that encodes STL task specifications as reward machines for guiding research pipeline decisions. Integrate with lumen-orchestrator to evaluate task progress against STL formulas.

---

The report at `/home/lumen/foreman/pilots/0d51b20dba5f/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
