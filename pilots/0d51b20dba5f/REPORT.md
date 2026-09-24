# PILOT 0d51b20dba5f report — reward machines for STL task specs in research pipelines

Runner: orch-burn-B-3f4634 (wq#2445), 2026-09-24. No arm was scored: the card's integration
target no longer runs. Nothing was built or changed.

## What was measured

| check | result |
|---|---|
| `lumen-orchestrator` unit files installed | 0 |
| where the unit is | `/etc/systemd/system/DISABLED-research-mill.bak/lumen-orchestrator.service`, dated 2026-08-23 |
| last orchestrator log line | 2026-08-08 07:09, "0 prompts, 0 high-value" |
| orchestrator script last modified | 2026-07-11 |
| research mill | retired 2026-08-28 (fleet notes) |
| `lumen-prompt-engine` | no unit; `prompt_engine.py` is a library module |
| existing STL or reward-machine code | none in `agent/` or `minerva/` source |

## Findings

1. The proposal says the autonomous research pipelines use heuristic planning and asks to integrate with
   `lumen-orchestrator`. That orchestrator has been parked with the retired research mill for about a month.
2. The metric the card names (research-to-action conversion) needs a live pipeline to measure. I did not check
   whether the remaining research flow (signal engine, research index, minerva proposal and eval) has anything
   a temporal spec could constrain.
3. The deferral wanted a specific pilot defined. None can be defined against a service that does not run.

## If someone still wants it

Name a live workflow with a stated temporal requirement (for example "every accepted proposal is evaluated within N hours
of filing") and monitor it offline against its logs first. That is a monitoring check, not a reward-machine build.

RECOMMENDATION: SKIP — integration target retired: 0 orchestrator units installed, last run 2026-08-08, parked with the research mill retired 2026-08-28; no scored arm.
