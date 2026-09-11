# wq#1723 — the router's effort-first downgrade, measured and deleted

**Decision: option B.** The downgrade branch and its `effort_down()` helper are gone from
`orchestrator/router.py`. Soft governor pressure (`tight`) no longer changes any ticket's
`(model, effort)`; the dispatcher's burn-rate concurrency ceiling
(`dispatcher/burn_rate.py`, wq#1699) is the only budget response.

Evidence script: `reports/wq1723_effort_first_downgrade.py` (read-only, rerunnable).
Corpus: 1261 worker attempts in `_runs/wq*/attempt-N.json` + `usage-N.json`, plus
`queue.db tasks.reopened_count`. Figures below are from the 2026-09-11 run.

## The question the ticket asked, and why it has no direct answer

> does lower effort measurably save pool % per ticket … vs the rework cost

The per-run pool delta cannot arbitrate this. The 5h pool is fleet-shared and up to
`CLOUD_CEILING` workers burn it concurrently, so what a run's
`rate_limits_exit - rate_limits_start` measures is mostly *how much other work overlapped
its window*, not its own spend. Section 3 of the script shows exactly that on 796 sonnet-5
attempts:

| correlation with the run's 5h delta | r |
|---|---|
| `elapsed_s` (how long the window was open) | **+0.279** |
| `turns` | +0.278 |
| `total_cost_usd` (the run's own spend) | **+0.171** |

The delta tracks wall-clock better than it tracks the run's own cost, and neither strongly.
So the load-bearing spend metric here is `total_cost_usd`, which *is* per-run attributed,
and the pool delta is reported only as a corroborating fleet-level figure.

One measurement trap worth recording: `resets_at` carries sub-second poll jitter within a
single window (`…15:29:59.58` at start vs `…15:30:00.05` at exit). Comparing it exactly to
exclude window rolls throws away **all 180** nonzero deltas in the corpus and reports a
flat zero for every bucket. The script allows a 120 s tolerance.

## What the downgrade actually buys, controlled for archetype

`claude-sonnet-5 / standard-engineering` is the only bucket with enough runs at two adjacent
efforts to compare like with like:

| | runs | mean 5h % | med cost | med turns | success % |
|---|---|---|---|---|---|
| `@high` | 428 | 1.32 | $1.669 | 59 | **52.3** |
| `@medium` | 25 | 0.56 | $1.512 | 45 | 48.0 |
| `@low` | 6 | 0.00 | $0.439 | 21.5 | 66.7 (n=6, not usable) |

A downgraded run is **9.4 % cheaper**. That is the entire upside.

## What it costs, per ticket

Grouping sonnet-5 tickets by the effort they actually ran at (tickets that changed tuple
mid-life are excluded, so each row is one tuple throughout):

| effort | tickets | mean attempts | needed >1 attempt | mean `reopened_count` | reopened at all |
|---|---|---|---|---|---|
| `high` | 469 | **1.67** | 37.7 % | 0.05 | 4.3 % |
| `medium` | 76 | **1.82** | 46.1 % | 0.11 | 10.5 % |
| `low` | 8 | 2.38 | 50.0 % | 0.12 | 12.5 % |

Medium needs **9.0 % more attempts** — it gives back almost exactly the 9.4 % it saved.

**Spend per ticket = cost/run × attempts/ticket:**

| | per run | × attempts | **per ticket** |
|---|---|---|---|
| `@high` | $1.669 | 1.67 | **$2.79** |
| `@medium` | $1.512 | 1.82 | **$2.75** |

A 1.4 % difference on n=76 vs n=469. That is a wash, not a lever.

And the ticket does not come out the same on the other side: success rate drops
52.3 % → 48.0 %, the multi-attempt rate rises 37.7 % → 46.1 %, and the reopen rate rises
4.3 % → 10.5 %. Per *successful* ticket the downgrade is strictly worse.

The unconditioned headline agrees in direction. Fleet-wide, `sonnet-5@medium` burns more
pool per run than `@high` (mean 1.42 % vs 0.98 %) and succeeds less (49.3 % vs 57.5 %); on
opus, the `xhigh → high` step the downgrade would take moves mean pool 1.00 % → 1.75 %.

There is also a compounding path. wq#779 F3 already walks the base chain forward one rung
per recorded fail, so the extra attempts a downgrade induces *also* push the ticket down
the model chain — the downgrade pays for itself in exactly the currency the chain walk
also spends.

## Honest limits on this evidence

- `medium` is n=25 runs / 76 tickets against n=428 / 469 for `high`. The parity finding
  rests on a modest sample; what makes it decisive is that three independent metrics
  (attributed cost per ticket, success rate, reopen rate) point the same way, and that the
  pool-% criterion the ticket proposed is unmeasurable per run in principle.
- `reopened_count` only exists from 2026-08-22 (wq#841), with 31 nonzero rows fleet-wide.
  It corroborates; it does not carry the argument. Attempts-per-ticket, which covers the
  whole corpus, does.
- Tickets are not randomly assigned to efforts, so some of the `medium` success gap may be
  selection (harder work routed down under pressure) rather than causation. That cuts
  *against* the downgrade either way: if pressure-downgraded tickets are the ones that then
  need rework, the downgrade is at best not helping them.

## Why not option A (per-spawn override)

Option A was the right shape for the wq#1699 bug — an override passed to fworker via
`dispatch_cmd`'s existing `--effort` argument would have been re-evaluated each attempt and
never stamped onto the wq tuple, so no transient `tight` reading could become permanent.
It was rejected because the measurement says the *behaviour* is not worth wiring: it would
carefully deliver a change that buys 9.4 % per run, gives it back in rework, and costs
4 points of success rate. The fix for a dead branch that should not run is deletion, not
careful plumbing.

## What changed

- `orchestrator/router.py` — the `p == "tight"` branch in `select()` and the `effort_down()`
  helper deleted; doctrine 3 in the module docstring rewritten; a `soft pressure noted`
  reason line kept so a dispatch pass still records that the governor was pressured.
- `orchestrator/tests/test_router.py` — `test_effort_first_downgrade_under_soft_pressure`
  replaced by `test_soft_pressure_leaves_the_tuple_alone` (asserts `tight` selects exactly
  what `ok` selects, on every archetype) and `test_effort_down_helper_is_gone`.
- `dispatcher/DESIGN.md`, `orchestrator/README.md`,
  `research/DISPATCH_GOVERNOR_WALKTHROUGH.md` sec 3 — documented that concurrency is the
  only budget response and that the walkthrough's "dead on the dispatch path" finding is
  resolved by deletion.
