# PILOT 3d071a263025 — Incorporate OfficeVal benchmark for long-horizon agent evaluation

Experiment card. Generated 2026-09-12T09:10:34+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `3d071a263025` |
| Deferred | 2026-08-08 |
| Impact / effort / risk | M / L / S |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/3d071a263025/REPORT.md` |

## Why this is a pilot and not a research prompt

The benchmark addresses a real gap (no eval harness) but its office-suite tasks are not clearly aligned with Lumen's homelab/orchestration duties; deferring while we evaluate mapping feasibility avoids wasted effort.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

(Filled by the wq#1823 run, before scoring the treatment — see REPORT.md
"Card fill-in disclosure" for why the card arrived blank; same class of gap
as ISSUE-LOG #257(b).)

A text-only Lumen agent (no visual document rendering, no LibreOffice/Office
COM) can raise mean OfficeVal dim2 score-ratio on a fixed 3-task sample by at
least 20 percentage points over baseline (Lumen's current capability: zero
office-editing tooling).

## Primary metric

Mean `total_score / max_score` (dim2 score-ratio, unitless, range 0-1) across
the 3 fixed-sample tasks, dim1 (open/parse) gate must stay `true`. Win = mean
treatment ratio − mean baseline ratio ≥ 0.20 (20 points), fixed before the
treatment edits below were attempted.

## Fixed sample

3 tasks from `data/task-en/` in the cloned
`baidu-frontier-research/OmegaUse-OfficeVal` repo (commit at clone time,
2026-09-13), chosen as the fewest-origin-file English-instruction tasks:
`officeval_002` (2 files), `officeval_005` (2 files), `officeval_010`
(1 file). Origin files pulled from the linked HF dataset
`baidu-frontier-research/OmegaUse-OfficeVal` (not gated, verified public).
Both arms evaluate the same 3 task IDs with each task's own bundled verifier
(`verifiers/officeval_0{02,05,10}_verifier.py`).

## Baseline (measure FIRST)

Run each task's `evaluate(dir_path)` against the **unmodified** origin
`.docx` — this is what Lumen does today (zero office-editing capability, so
"do nothing to the file" is the honest baseline arm):

```
python3 pilots/3d071a263025/run_eval.py officeval_002 <dir-with-unmodified-docx>
python3 pilots/3d071a263025/run_eval.py officeval_005 <dir-with-unmodified-docx>
python3 pilots/3d071a263025/run_eval.py officeval_010 <dir-with-unmodified-docx>
```

## Treatment

The smallest real edit per task that a text-only agent (python-docx/lxml, no
rendering) could plausibly make, applied for real and re-scored with the
same verifiers. `officeval_005`'s mechanical page-geometry rule (A4 portrait,
3/2.5/3/2.5cm margins) was fixed via `fix_005_margins.py`. `officeval_002`
and `officeval_010` were assessed but not edited within the 90-min box once
the verifiers' remaining rules turned out to require reference-image/PDF
visual comparison or per-paragraph font/spacing specs across dozens of
items (`officeval_010_verifier.py` alone is 2190 lines of OOXML shape
geometry checks) — see REPORT.md for the rule breakdown.

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

Lumen currently has no formal experiment/eval harness (see 'No experiment/eval harness' gap). Agent orchestration services (lumen-orchestrator, lumen-planner, lumen-generator, lumen-evaluator) operate without benchmark-based regression testing.

## Proposal — proposed change

Introduce a new evaluation service (lumen-officeval-eval) that runs a subset of OfficeVal tasks against Lumen's existing agent orchestrator in a containerized LibreOffice environment, providing a quantitative baseline for long-horizon task performance. The harness will be isolated from production pipelines.

---

The report at `/home/lumen/foreman/pilots/3d071a263025/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
