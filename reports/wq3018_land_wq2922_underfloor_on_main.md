# wq#3018 judgment: wq#3007's CONTRADICT of wq#2922 — upheld, and landed

**Decision: option (1), land `c1301b79` as-is.** The rebase is clean, the fix is correct
against current `main`, and re-deriving it would have thrown away a diff that three
separate readings (its author's, wq#3007's cold review, and this one) agree on. What was
missing was never the code. It was the merge.

wq#3007's verdict is confirmed on every point, and the class is now closed **on `main`**.

## 1. The rebase (ticket item 1)

`git cherry-pick -n c1301b79` onto `e58f9b99` applied with **no conflicts** in all four
files. `main` did move a long way under the branch — but not through this code. The
`~1105 → 999` line change the ticket flagged is wq#2913 removing the stop-based
multi-pass design, which landed *before* `c1301b79` was cut, so the branch was already
sitting on top of it; nothing reworked `_body()`, `_matches()`, `mask_text()` or `scan()`
afterwards. Checked by reading, not by trusting the clean apply:

- `_body()`'s MASK-literal guard (`masking.py:111-126`) is the same guard the fix backs,
  and the false claim wq#2922 was required to correct — "paid in the direction that
  alarms rather than the one that goes silent" — is now true rather than aspirational,
  with the reason in the docstring at `masking.py:117-126`.
- `_orphan_matches()` (`masking.py:670-718`) consumes `_B`, `_OWN_TAIL`, `_body()` and
  the six `_PRE_*` constants — all present and unrenamed on `main` (`masking.py:64-90`,
  `600`).
- Its floors and charsets (`masking.py:469-476`) match the `PATTERNS` entries they back
  (`masking.py:369-389`) exactly, `aws` included (`{16}` fixed-width, floor 16).
- Wiring: `mask_text()` appends orphan labels to `hits` and nothing to `spans`
  (`masking.py:943-946`); `scan()` appends a record with no `context` key
  (`masking.py:1072-1078`). The one consumer of that key reads it with `.get()` and
  passes it to a `t or ""` (`harness/fworker.py:1168` → `harness/cf_hold.py:51-53`), so
  the omission is safe — the same shape `suppressions()` already ships.

## 2. The mirrors (ticket item 2)

- **Daemon.** `token_engine_daemon._ORPHAN_SHAPES` (`token-engine/token_engine_daemon.py:265-272`)
  matches that file's own current `MASK_PATTERNS` floors and charsets
  (`:231-250`) — no drift since `c1301b79` was cut. `mask_hits()` appends orphan names
  after `mask_read()` (`:437-442`).
- **sed wrapper.** The claim holds, and holds for free: `ops/wq2316-mask-secrets-boundary/mask-secrets`
  on current `main` is already a 61-line wrapper that execs `masking.main` and *refuses
  to pass input through* if it cannot find a `masking.py` (`:38-61`). wq#2913 replaced
  the hand-mirrored sed program; there is no third copy of the rules left to drift.

## 3. Evidence — green ON MAIN

Baseline first, on `main` with nothing applied, so the numbers below can be read as a
delta and not an assertion:

```
$ python3 reports/wq2868_probe_underfloor_class.py          # e58f9b99, no fix
B under-floor via MASK guard (leak = fail): 96 cases, 96 failing
    github/off0/hits=SILENT   ... and 88 more
$ python3 -m pytest harness/tests/test_mask_underfloor_invariant.py -q
4 passed, 1 xfailed
```

With the fix on the same tree:

```
$ python3 reports/wq2868_probe_underfloor_class.py
B under-floor via MASK guard (leak = fail): 96 cases, 0 failing     # 0 SILENT
TOTAL FAILING 0

$ python3 reports/wq3018_probe_orphan_backstop_on_main.py
B under-floor via MASK guard (leak = fail) [on main]: 96 cases, 0 failing
orphan backstop quiet on real committed text: 14 cases, 0 failing
daemon mirror closes the same class: 96 cases, 0 failing
TOTAL FAILING 0

$ python3 -m pytest harness/tests/test_mask_underfloor_invariant.py -q
9 passed            # xfail-strict dropped; the test passes for real
$ python3 -m pytest harness/tests/test_mask*.py -q
1129 passed in 333.10s
```

## 4. What this ticket adds beyond the cherry-pick

wq#3007 named two risks it could not close from a verify seat. Both are now tests, and
each was shown able to fail before being believed (wq#2630).

**The fail-loud backstop's own failure mode is over-firing.** Section B cannot detect it:
all 96 of its cases are shapes built to trip, so it measures only the direction the fix
was designed for. `reports/wq3018_probe_orphan_backstop_on_main.py` section 2 sweeps 14
*real committed files* instead — `masking.py` itself, its four test modules, the daemon,
the sed wrapper, three mask reports, `README`, `BUILD_PLAN`, the public runbook, and
`kb/ISSUE-LOG.md` from a different repo entirely — every one of them read at a pinned rev
(`e58f9b99`, `kb` at `26e286f`) so the run repeats. These files write `<prefix>MASKED` by
the hundred: they are the exact residue wq#2638 spent a ticket teaching this guard to
ignore. 0 hits. Negative control: swapping `_orphan_matches()` for the weaker design its
own docstring names as wrong — the bare "does the charset continue past the literal"
check, no combined-floor test — turns the same 14 files into **80** false alarms. The
combined-floor test is load-bearing, and section 2 can see when it stops being.

**`_ORPHAN_SHAPES` names each floor and charset a second time rather than deriving it.**
wq#3007 flagged this as a drift risk in principle and found no live drift; it was right
on both counts, but nothing was watching. Nothing *could* be: section B builds its cases
from the probe's own independent `RULES` table, so a floor that drifts in both masking
tables at once still reads green there. Two guards now pin it —
`test_the_orphan_table_has_not_drifted_from_the_rules_it_backs` (to the `PATTERNS` entry
each shape backs, by the body sub-expression they must share) and
`test_the_daemon_orphan_mirror_has_not_drifted_from_maskings` (to the daemon's hand
copy). All four injected drifts — a raised floor, a widened charset, a shape backing no
rule, a daemon copy left behind — fire.

## 5. Ticket item 3: the gate, filed separately

wq#3007's root cause is right and is not this ticket's to fix: `proof_type: citation` is
checked against **the worker's own worktree diff** (`harness/acceptance.py:648-661`,
`_check_citation`), never against `main`, so citing `file:line` in an unmerged branch
satisfies it exactly as well as citing landed code. That is how a ticket closes `done`
while the vulnerability it documents stays open.

The mechanism to fix it already half-exists and was not reached for: `wq add
--deliverable PATH` (wq#1673) *does* refuse `done` unless the path is on `main`, and
`acceptance.py:514-517` already special-cases an `ops/wqNNNN-*/install.sh` citation as
proof the script landed. Generalising that — every citation re-checked against `main` at
`done`, not against the branch — is the structural fix. Filed as its own ticket rather
than scope-crept here.

## 6. One unrelated red found, not fixed

`test_burn_filter.py::test_no_tracked_module_pages_when_a_worker_reads_it` fails on
`main` today, **pre-existing and nothing to do with this ticket**: its tree-wide sweep
now matches zero of 265 tracked modules, so its own anti-vacuity guard fires
(`"sweep is vacuous: 0 files match a pattern"`). Measured under both `main`'s
`masking.py` and this branch's over the identical file list — `0 files match` either way,
so the fix neither caused it nor hides it. The corpus emptied as wq#2288/wq#2297 cleaned
the false-positive-shaped identifiers out of the source the test reads. Filed separately;
the test is doing its job by refusing to pass vacuously.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
