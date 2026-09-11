# wq#1777 — DGM v2 rotation wheel: DO NOT BUILD (design superseded by retirement)

## The finding (wq#1715 F1)

`DGM_V2_ROTATION_DESIGN.md` §2 (in `/home/lumen/autonomy/agent5/`, now the
retired-services archive) specifies six gene classes on a weekly wheel:

| gene | target |
|------|--------|
| G1 | evaluator thresholds |
| G2 | retry budget |
| G3 | MODEL_CHAIN order |
| G4 | generator_sys |
| G5 | planner_sys |
| G6 | evaluator_sys |

The wheel was never implemented. `dgm_v2.py` hardcodes
`target='generator_sys'` at lines 92 and 103; `planner_sys`/`evaluator_sys`
sit in the ROLE map but are never selected. All 86 v2 cycles in
`.dgm_history.jsonl` targeted `generator_sys` — five of six gene classes
went unmutated for three months.

## Decision

**Do not implement the wheel. The rotation design is superseded, not
fixed.** This ticket was explicitly dependent on the wq#1715 F2 outcome,
which resolved as **retire lumen-dgm entirely**
(`ops/wq1778-dgm-retire-decision/RUNBOOK.md`):

- `config_registry.json` — the only thing DGM's mutations ever promoted
  into — has no reader outside DGM's own benchmark harness. The production
  consumers (`lumen-planner`/`-generator`/`-evaluator`) are
  `LoadState=not-found` and the agent5 split is formally retired
  (`docs/fleet/retired_services.yaml`, `agent5-split` entry).
- Building the wheel would multiply the mutation surface (1 gene → 6) on a
  genome with no consumer — six times the daily benchmark spend producing
  DISCARD lines about prompts nothing reads.
- Even on its own benchmark the one-gene search had plateaued: 3 promotes
  in 91 cycles, all in one August week, none in the 18 days before
  retirement; the last four cycles' bootstrap CIs all straddled zero.

## Why the amendment lives here and not in the design doc

wq#1778's data disposition declares `/home/lumen/autonomy/agent5/`
(registry, history, benchmark, code, design docs) an untouched archive.
Editing `DGM_V2_ROTATION_DESIGN.md` in place would mutate the archive to
describe a system that no longer runs. This file is the amendment of
record; anyone reading the archived design doc should be routed here via
the retired-services entry.

## Disposition of the six gene classes

Unbuilt and staying unbuilt. If prompt-optimization is ever revived for a
*live* consumer (fworker worker prompt, prompt-forge templates), that is a
new project with its own genome format, benchmark, and promote path —
proposed on its own merits with the 91-cycle DGM history as prior evidence
(see wq#1778 RUNBOOK "Rewiring ... rejected, not ignored"). The wheel
design is input to that proposal, not a debt to be paid on DGM.

## Related

- `ops/wq1778-dgm-retire-decision/RUNBOOK.md` — the retirement decision and
  host-side apply steps (filed as wq#1779).
- wq#1715 F1/F2 — the audit that surfaced both findings.
