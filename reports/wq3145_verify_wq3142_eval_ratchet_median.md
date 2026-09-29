# wq#3145 — verify wq#3142 [mailbot] eval ratchet compares one noisy sample to one noisy baseline

Archetype 8 verify pass. Author of wq#3142: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance, same model as author).

PR under test: mailbot#29 (branch `feat/wq3142-mailbot-eval-ratchet-compares-one-noisy--a1`),
5 files: `deploy.sh`, `evals/live_baseline.json`, `evals/run.mjs`, `evals/run.sh`,
`evals/run.test.mjs` (+335 / -95).

## Verdict

CONFIRM, with two hardening notes and one design limit. Nothing here blocks a deploy.

## Checked

- Read the full diff cold. `run.sh` runs the suite N times (default 3) against fresh wrangler
  instances and hands the N reports to `run.mjs --aggregate`. That aggregate is what gates
  `deploy.sh`; run #1 no longer does. A run that exits 2 aborts the whole gate instead of
  aggregating a broken sample.
- `node --test evals/run.test.mjs` in a detached worktree of the PR branch: 8 pass, 0 fail.
- Probed `aggregateReports` and `evaluateGate` directly with hand-built reports:
  - one lucky run among three (passed 3, 4, 12) aggregates to 4 and fails the ratchet against the
    live baseline of 7/15, so a single good sample cannot carry a bad candidate;
  - mismatched corpus size across runs throws instead of averaging;
  - an even N gives a half-integer median (N=2: 7.5).
- `deploy.sh` now rewrites `live_baseline.json` from the aggregate report and keeps the spread, so
  the bar moves only on a median. Because the ratchet needs candidate median >= baseline median per
  suite, the recorded baseline cannot go down.

## Hardening notes (not blockers)

1. A run whose report lacks a whole category (for example zero scored items without a crash) is
   dropped from that category's median, not counted as a failure. In practice `run.mjs` runs all
   three categories every time, so this needs a run that yields zero items without exit 2.
2. `aggregateReports` treats an item with no `redTape` field as clean. `score.mjs` always sets it,
   so real reports are fine; only a hand-edited or truncated report would slip through.

## Design limit

Both sides are now medians of 3, which cuts the noise but does not remove it. The baseline itself
was measured with spread [7,7,8] on normal, so a candidate of equal true quality can still land a
median below the baseline and fail. Expect an occasional false FAIL on a tie; a rerun is the
right response, not SKIP_EVAL_GATE.
