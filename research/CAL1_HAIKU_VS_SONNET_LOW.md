# CAL-1 leftover — deliberate haiku vs sonnet@low A/B (wq#780)

wq#750's audit (`research/MODEL_ROUTING_AUDIT.md` §2) found the haiku-vs-sonnet@low
placement can't be closed by observation: haiku is the primary for no archetype, sits
behind a pressure gate that has never fired (13% session utilisation), and drew zero
launches in 149. wq#779 landed the attempt-count downgrade trigger this ticket was
blocked on; this doc is the deliberate paired trial that follows.

## Method

Four task templates, each dispatched twice — once pinned to `claude-haiku-4-5` (no
`--effort`, it supports none) and once to `claude-sonnet-5@low` — via `wq add
--model ... --archetype ...` then `harness/fworker.py run --ticket ID --repo
/home/lumen/foreman`. Same task text both arms, so this is a true paired design, not
a match on archetype alone. Two archetypes covered per the ticket's ask:

| pair | archetype | task |
|---|---|---|
| A | mechanical-edit | add a `--version` flag to `harness/reaper.py` |
| B | mechanical-edit | add PEP 484 type hints to `orchestrator/judgment.py` |
| C | probe-build | new read-only census script over `queue.db` |
| D | probe-build | new unit test file for `make_worktree()` branch naming |

`ARCHETYPE_MAX_TURNS` is 600 for every archetype as of `62fb976`/`300e903` (Frank's
turn-ceiling fix), so both arms already ran under the identical, generous ceiling the
ticket asked to control for — nothing extra to pin there.

wq#807 (an initial mechanical-edit-A/haiku dispatch) was excluded: it was launched
with a mistaken `--wall-clock-timeout 30` smoke-test flag and got killed twice by
that, not by the model. It was closed `--no-artifact` and replaced by wq#815 for a
clean run. All 8 tickets counted below (`815/808`, `809/810`, `811/812`, `813/814`)
ran with fworker's real default wall-clock ceiling (3600s).

## Results

| pair | haiku wall-clock | sonnet@low wall-clock | haiku vs sonnet@low | both SUCCESS? |
|---|---|---|---|---|
| A (wq815/808) | 45s | 41s | +10% | yes/yes |
| B (wq809/810) | 90s | 57s | +58% | yes/yes |
| C (wq811/812) | 66s | 43s | +53% | yes/yes |
| D (wq813/814) | 91s | 61s | +49% | yes/yes |
| **mean** | **73s** | **51s** | **+44%** | **4/4 both arms** |

Wall-clock is `done_at - claimed_at` from the wq record — real elapsed time for the
whole attempt, not a self-report. Every SUCCESS was independently re-verified here
(not just trusted from the worker's own `wq done` note): B and D re-ran the actual
test suite the worker claimed passed (`orchestrator/tests/` 28/28 on both arms' B
work; `test_worktree_naming.py` 6/6 haiku, 5/5 sonnet@low on D), and A/C were
inspected as small, self-evidently-correct diffs. No FALSE_COMPLETE.

**Turns**: not captured for these 8. `fworker.py`'s own `attempt-N.json` (which
records `turns`) never got written for any of the 8 when dispatched via a backgrounded
subprocess from inside this sandboxed session — the run completed for real (worker
self-reported via `wq done`, wall-clock and diffs are real) but the supervisor's
post-attempt bookkeeping didn't land on disk. A same-session foreground control
dispatch (wq#817, diagnostic-only, haiku, trivial one-line task) did capture it
cleanly: `turns=17 elapsed=47s`, confirming the write path itself works — the gap is
specific to how this session backgrounds a subprocess, not a haiku-vs-sonnet
difference. Worth its own ticket if turns-level precision is wanted later; wall-clock
above is a real, if coarser, substitute for this trial.

## Reading it

- **No correctness gap.** Both models hit 4/4 SUCCESS with independently-verified
  passing tests, on the same tasks, same ceiling. Placing haiku as "unreliable" would
  be false.
- **Real latency gap.** Haiku ran 44% longer wall-clock on average, worse on every
  pair except the trivially small A. This tracks the manifest's existing "different
  shape, not just cheaper" quirk note — plausibly more exploration/turns per unit of
  work, consistent with the one turns data point available (17 turns for a one-line
  change).
- **N is small** (4 pairs) and every task was deliberately kept short enough to
  survive this session's own dispatch constraints — none of the four ran long enough
  to test whether haiku's relative slowdown holds, shrinks, or grows on harder
  probe-build/mechanical-edit work. Treat the 44% figure as a directional first
  calibration, not a precise constant.

## models.yaml

Updated the haiku `trust:` line with this measurement, replacing the "Place vs
sonnet@low = CAL-1" placeholder. Placement: haiku stays the downgrade rung behind
sonnet@low (as already wired by wq#779), not promoted to a primary — it has no
measured correctness edge and is measurably slower, only cheaper.

## Artifacts

wq#807 (excluded, closed `--no-artifact`), wq#808/810/812/814 (sonnet@low arms),
wq#811/813/815 (haiku arms, wq#815 replaces excluded wq#807), wq#809 (haiku arm),
wq#817 (diagnostic-only, not part of the trial). PRs #64-71 on `claude-foreman`
(Gitea) are the eight trial diffs — calibration throwaways per each ticket's own
detail text, not intended to land; safe to close without merging.

## wq#3618 re-run: claude-haiku-5-5 vs sonnet-5@low (2026-10-07)

CC v2.1.293 made `claude-haiku-5-5` the default Haiku. Same four tasks, rerun with
`harness/cal1_pair.py`. It uses the real `fworker.py run` in the `cal1_fixture.py` sandbox:
scratch wq DB, lumen-pr shimmed to `/bin/false`, and a clone of foreman pinned to
`8d614840` with its remote removed, so no trial PRs were opened this time. Arms ran
interleaved per pair. Each arm was scored by a check command run in its worktree, and
every check fails on the unmodified base. A: `reaper.py --version` exits 0. B:
judgment.py changed, has `->`, and `orchestrator/tests` pass. C: a new non-test script
opens with `mode=ro`. D: the new test file passes.

| pair | haiku-5-5 wall / turns | sonnet@low wall / turns | haiku-5-5 vs sonnet@low | checks |
|---|---|---|---|---|
| A | 50.0s / 15 | 56.2s / 9 | -11% | PASS/PASS |
| B | 90.4s / 40 | 95.1s / 32 | -5% | PASS/PASS |
| C | 79.4s / 21 | 84.7s / 21 | -6% | PASS/PASS |
| D | 71.3s / 20 | 133.2s / 19 | -46% | PASS/PASS |
| **mean** | **72.8s / 24** | **92.3s / 20** | **-21%** | **4/4 both** |

All 8 classified SUCCESS and closed `done`. Wall-clock is fworker's own elapsed time. The
first scoring flagged C/haiku FAIL because the check grabbed the arm's
`test_wq5_queue_census.py` instead of the script. That was a check bug: the script does
open with `mode=ro`, and both arms' C tests pass (3/3 and 2/2). It was fixed and rescored
(`cal1_pair.py rescore`).

Reading: haiku-4-5's +44% latency penalty is gone. haiku-5-5 beat sonnet@low on every
pair while using a few more turns, with no correctness gap. The CLI's `total_cost_usd`
cannot be used here. CLI 2.1.282 doesn't recognise the model
(`[claude-code:unrecognized_model]`) and bills a 29K-token canary at $0.085, where
$0.10/$0.50 pricing would come to about $0.0015.

Placement: haiku-5-5 replaces haiku-4-5 as probe-build's downgrade rung and takes the
`haiku` alias. It is not promoted to a primary: n=4, and sonnet@low owns mechanical-edit
on yield (wq#750). haiku-4-5 stays LIVE for explicit pins and as the self-heal auth-probe
model (`dispatcher/self_heal.py`), the one model that cannot be org-disabled. Raw results:
`/tmp/fworker-wq3618/cal1pair/pair_results.json` (scratch, not durable). Rerun with the
command in the `cal1_pair.py` docstring.
