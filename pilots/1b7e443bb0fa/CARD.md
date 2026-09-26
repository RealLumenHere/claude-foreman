# PILOT 1b7e443bb0fa — Adopt DWM-style action-effect separation in planning

Experiment card. Generated 2026-09-25T20:34:36+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. The five spec sections below are authored by the generator, not
by the runner (wq#2690, `research/DEFER_PILOT_LANE.md` §3.1a): a runner that finds
them missing, or finds a DO-NOT-RUN banner, blocks the ticket rather than inventing
a measurement plan. Never edit the card during a run.

| | |
|---|---|
| Proposal | `1b7e443bb0fa` |
| Deferred | 2026-08-10 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/1b7e443bb0fa/REPORT.md` |

## Why this is a pilot and not a research prompt

Deferred for a measurement; pilot wq#1953, card /home/lumen/foreman/pilots/1b7e443bb0fa/CARD.md. Original: The paper is conceptual and not a concrete tool; applicability to Lumen's specific pipelines is uncertain, and existing eval harnesses already measure improvements. Defer until a concrete integration path is identified.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

On a pinned 50-task planning log, DWM-style action-effect separation (dwm_separator.py) beats raw-observation planner on planning_revision_rate by at least 10 percent fewer revisions per task.

## Primary metric

planning_revision_rate: mean number of planner re-invocations per task required before the emitted plan passes the existing validator; win = dwm arm mean is <= 0.90 x baseline arm mean (i.e., >=10% fewer revisions/task) on the same 50-task pinned log; unit = revisions per task.

## Fixed sample

/home/lumen/foreman/pilots/1b7e443bb0fa/card.md plus /data/lumen/logs/planner_tasks_50.jsonl (exactly 50 rows, sha256 pinned at run start by both arms; both arms must consume the same file).

## Baseline (measure FIRST)

```
sha256sum /data/lumen/logs/planner_tasks_50.jsonl
python3 /data/lumen/lumen-planner/run_eval.py --tasks /data/lumen/logs/planner_tasks_50.jsonl --mode raw-observation --out /tmp/baseline_plan.json
python3 /data/lumen/lumen-planner/score_revisions.py --plan /tmp/baseline_plan.json --tasks /data/lumen/logs/planner_tasks_50.jsonl --out /tmp/baseline_score.json
```

## Treatment

```
sha256sum /data/lumen/logs/planner_tasks_50.jsonl
cat > /data/lumen/dwm_separator.py <<'PY'
import json,sys
for line in sys.stdin:
    rec=json.loads(line)
    rec['action_effect']=rec.get('action_delta',{})
    rec['env_effect']={k:v for k,v in rec.get('state_delta',{}).items() if k not in rec.get('action_delta',{})}
    print(json.dumps(rec))
PY
python3 /data/lumen/dwm_separator.py < /data/lumen/logs/planner_tasks_50.jsonl > /tmp/decomposed_50.jsonl
python3 /data/lumen/lumen-planner/run_eval.py --tasks /tmp/decomposed_50.jsonl --mode dwm-separated --out /tmp/dwm_plan.json
python3 /data/lumen/lumen-planner/score_revisions.py --plan /tmp/dwm_plan.json --tasks /data/lumen/logs/planner_tasks_50.jsonl --out /tmp/dwm_score.json
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

Lumen's planning and self-improvement pipelines (e.g., lumen-planner, lumen-dgm) do not explicitly separate action effects from environmental changes; they rely on raw observations and existing eval harnesses.

## Proposal — proposed change

Integrate DWM concepts by adding a lightweight module that decomposes state changes into action-driven vs. environment-driven components, used to refine planning inputs. This could be a new script or library, e.g., /data/lumen/dwm_separator.py, with a systemd timer or integration into existing planner.

---

The report at `/home/lumen/foreman/pilots/1b7e443bb0fa/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
