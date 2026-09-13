# PILOT REPORT 3d071a263025 — Incorporate OfficeVal benchmark for long-horizon agent evaluation

Run: 2026-09-13, headless BUILD worker (wq#1823), worktree `wq1823-a1`.

## Node substitution (disclosed, precedent: ISSUE-LOG #257)

CARD.md says "Run on hathor." `fleet-exec run hathor '...'` denied with
`DENY [grant:grant_missing_env]` — same class-D1 defect ISSUE-LOG #257
already logged for wq#1560, the first run of this same pilot lane
(`fworker.resolve_exec_grant` only writes a grant file when the ticket's
`needs:` tag is non-empty; `wq show 1823` confirms `needs:` is blank here
too, so the fleet-exec-policy.yaml permission for hathor exists but the
grant file never gets created). Raw `ssh hathor@100.80.71.13` is not on the
worker Bash allowlist either (only `fleet-exec` is). Per the #257 precedent
this run measured both arms on Lumen itself against the same fixed sample —
this changes absolute wall-clock only, not the primary metric (a ratio of
verifier scores that is host-independent).

## Card fill-in disclosure (precedent: ISSUE-LOG #257(b))

CARD.md arrived with Hypothesis/Primary metric/Fixed sample/Baseline/
Treatment as empty template comments (same generator gap as #257(b): the
card generator writes the raw template without filling the spec). Filled
in before the treatment edit was scored, per the card's own "edit it before
the run" clause — the win bar (≥20 points mean score-ratio delta) was fixed
before any treatment edit was attempted or scored, and 2 of 3 baseline
numbers were already known when the bar was set, but the bar is a fixed
round number chosen ahead of the treatment attempt, not tuned to the gap.

## What OfficeVal actually is (not what the proposal described)

The proposal text says the harness needs "a containerized LibreOffice
environment." That's inaccurate for the actual upstream repo
(`baidu-frontier-research/OmegaUse-OfficeVal`, cloned live from GitHub this
run): verification is static OOXML/PDF parsing (`lxml`, `python-docx`,
`pdfplumber`) — no LibreOffice, no rendering, no container needed for 91 of
100 verifiers (the other 9 need Windows + Office COM, which this run never
touched). This lowers the integration-effort estimate but does not change
the task-content mismatch the deferral flagged.

## Fixed sample

`officeval_002` (2 origin files), `officeval_005` (2 origin files),
`officeval_010` (1 origin file) — the 3 fewest-origin-file English-
instruction tasks in `data/task-en/`, origin `.docx`/`.pdf` pulled from the
public (non-gated) HF dataset.

## Baseline (measured)

Verifier run against each task's **unmodified** origin `.docx` (dim1 =
parses/opens fine for all 3; dim2 = task-specific rubric):

| task | dim1_pass | total_score | max_score | ratio |
|---|---|---|---|---|
| officeval_002 | true | 3 | 13 | 0.231 |
| officeval_005 | true | 4 | 28 | 0.143 |
| officeval_010 | true | 0 | 90 | 0.000 |
| **mean** | | | | **0.125** |

Raw: `results_baseline_002.json`, `results_baseline_005.json`,
`results_baseline_010.json`.

## Treatment (measured)

Real edit attempted and scored for `officeval_005`: page geometry fixed to
A4 portrait, 3/2.5/3/2.5cm margins via `fix_005_margins.py` (python-docx,
no rendering) — a genuine, verifiable improvement:

| task | dim1_pass | total_score | max_score | ratio | edit attempted |
|---|---|---|---|---|---|
| officeval_002 | true | 3 | 13 | 0.231 | none (see below) |
| officeval_005 | true | 7 | 28 | 0.250 | margins/page size |
| officeval_010 | true | 0 | 90 | 0.000 | none (see below) |
| **mean** | | | | **0.160** | |

Raw: `results_treatment_005.json`.

**Delta: +0.036 (3.6 points) mean score-ratio, against a pre-registered win
bar of ≥0.20 (20 points). Miss.**

`officeval_002` and `officeval_010` were assessed for cheap wins and none
were found inside the 90-min box:

- `officeval_002`'s only unmet items beyond the one already passing
  (5+5 of its 13 points) require inserting two parallel header lines with a
  ≥3:1 thickness ratio starting exactly at the abstract page boundary — this
  needs section/page-boundary detection plus header-border XML authoring,
  not a one-line fix.
- `officeval_010`'s verifier (`officeval_010_verifier.py`, 2190 lines) scores
  absolute shape geometry (VML/DrawingML box positions in cm, line lengths,
  arrow endpoints) reconstructed from a reference flowchart image. Every one
  of its ~20 dim2 items needs the edited flowchart's shapes placed within a
  few points of a spec that exists only as a picture, i.e. visual layout
  work a text-only agent cannot verify against without rendering the
  document — 0/90 baseline and 0/90 unattempted-treatment for the same
  reason.
- `officeval_005`'s remaining 24 unmet items are similar: per-page/per-
  paragraph font-family, size, spacing, and structural-order checks (cover
  page, table of contents, per-chapter heading styles) that assume the
  editor is reading the reference PDF's rendered pages, not its text.

This is the same shape as the deferral's own reasoning, now measured rather
than argued: the *evaluation mechanism* (static OOXML parsing) needs no
LibreOffice container as the proposal assumed, but the *task content*
(office-suite visual formatting fidelity) still does not match what a
text-only orchestration agent can produce or self-verify, and the one
mechanical win available (page geometry) is a small fraction of any given
task's total rubric (3 of 28 points here).

## Kill criteria check

Wall-clock: under budget. Baseline reproduced cleanly for all 3 tasks (no
baseline defect). Treatment was completed and scored for 1 of 3 tasks;
the other 2 were assessed but no edit found inside budget that would move
their score — per CARD.md's own kill criteria ("Treatment incomplete at
budget -> report partial numbers, RECOMMENDATION: WATCH").

## Recommendation basis

The measured delta (+3.6 points) misses the pre-registered win bar
(20 points) by a wide margin, and the miss is structural (verifiers need
visual/layout fidelity, not just structural edits) rather than an artifact
of only 1 task getting a real edit — the two untouched tasks' remaining
rubric items are dominated by the same visual-fidelity requirement that
made `officeval_005`'s own remaining 24 items unreachable. That said, the
literal kill-criteria clause for an incomplete-budget treatment is WATCH,
not SKIP, so this reports WATCH rather than overriding the card's own rule
with a stronger unilateral SKIP call.

RECOMMENDATION: WATCH — mean dim2 score-ratio improved baseline 0.125 to
treatment 0.160 (+0.036 / +3.6 points) against a pre-registered win bar of
+0.20 (20 points), a miss; the shortfall traces to OfficeVal's rubrics
needing visual/rendered-layout fidelity (confirmed by reading
`officeval_010_verifier.py`'s 2190 lines of shape-geometry checks and
`officeval_005`'s 24 unreached per-paragraph font/spacing items) that a
text-only Lumen agent cannot currently produce or self-verify; re-run only
if/when Lumen gains a document-rendering or visual-diff capability that
would let a wider treatment attempt close more than the ~3-point mechanical
slice measured here.
