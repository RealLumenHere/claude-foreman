# REPORT — PILOT 3d071a263025: Incorporate OfficeVal benchmark for long-horizon agent evaluation

## Setup

Card: `pilots/3d071a263025/CARD.md` (reconstructed for this run, see ISSUE-LOG #369 —
no card was ever generated for this proposal). Fixed sample: OmegaUse-OfficeVal tasks
`officeval_002`, `officeval_005`, `officeval_010` (commit `ffbeecb875244...`), each
scored by the benchmark's own per-rule verifier via `pilots/3d071a263025/aggregate_results.py`
over the raw verifier output in this directory (`baseline_{002,005,010}.json`,
`treatment_005.json` — copied verbatim from the prior attempt's `work/` scratch dir,
whose scores were computed with `work/run_eval.py <task_id> <dir>` against the
downloaded `.docx` files and are deterministic given those inputs).

## Results

```
$ python3 pilots/3d071a263025/aggregate_results.py
baseline:  7/131 = 0.053 (tasks 002,005,010)
treatment: 7/28 = 0.250 (task 005 only)
```

Per-task baseline compliance: `002`=3/13, `005`=4/28, `010`=0/90.

Treatment was only completed for task `005` (one manual docx section-margin fix,
`work/fix_005_margins.py`), raising that task from 4/28 (14.3%) to 7/28 (25.0%). Tasks
`002` and `010` never received a treatment attempt — the prior run was killed before
building fixes for them, so no aggregate baseline-vs-treatment comparison over the full
fixed sample exists. Per the card's kill criteria, this is reported rather than
extrapolated from the single completed task.

## What the sample's rules actually check

Sampling task `010`'s 90 sub-rules (0/90 baseline): exact font/size/weight for a
diagram title, gray divider-line color/thickness/length in cm and points, box fill/border
colors, and box-to-box spacing tolerances in a Chinese-language technical report diagram.
Tasks `002` and `005` are the same genre — header/footer text alignment, rule-line
thickness ratios, margin measurements — for Chinese-institution thesis formatting. None
of the sampled rules reference content correctness, task completion, or any behavior
resembling homelab/orchestration work (fleet ops, tool use, code, infra).

## Assessment against the defer reason

The original defer memo: "the benchmark addresses a real gap (no eval harness) but its
office-suite tasks are not clearly aligned with Lumen's homelab/orchestration duties;
deferring while we evaluate mapping feasibility avoids wasted effort." This pilot
confirms that concern directly:

- The harness itself works mechanically: `run_eval.py` loads OmegaUse-OfficeVal's
  verifiers and produces well-formed per-rule scores (evidence: 4 successful verifier
  runs, `status: "ok"` in all four JSON files in this directory).
- The task content is exact-measurement document typesetting (cm/pt tolerances, font
  weights, color hex/RGB matches) with no observed rule mapping to fleet/orchestration
  concerns.
- Building a treatment arm requires hand-authoring per-rule docx-formatting fixes
  (`fix_005_margins.py` fixed exactly one rule out of 24 still failing on that task) —
  effort that produces no reusable capability for Lumen's actual domain.

## RECOMMENDATION: SKIP — baseline=0.053, treatment(005-only)=0.250; benchmark harness runs cleanly but its office-typesetting rule set has no observed mapping to homelab/orchestration duties, confirming the original defer reason. Do not adopt; no further pilot spend warranted without a proposed rule subset that maps to Lumen's actual duties.

## Reconciliation (wq#1823 attempt 3, 2026-09-15)

This pilot's deliverable was already merged to `main` in commit `1d2726f` ("chore(pilot):
OfficeVal benchmark pilot 3d071a263025, verdict SKIP", `Refs: wq#1823`), before this
attempt's branch was cut — `git diff main` in this worktree was empty at the start of
this attempt. Re-ran the scoring to confirm the merged numbers still reproduce exactly:

```
$ python3 pilots/3d071a263025/aggregate_results.py
baseline:  7/131 = 0.053 (tasks 002,005,010)
treatment: 7/28 = 0.250 (task 005 only)
```

Matches the Results section above bit-for-bit. Verdict stands: SKIP. See
`/home/lumen/kb/ISSUE-LOG.md` #397 for the redispatch-of-already-merged-ticket process
defect this attempt uncovered.
