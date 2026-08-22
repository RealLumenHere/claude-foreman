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
