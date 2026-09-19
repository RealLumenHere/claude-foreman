# wq#2029 — verify-pass: wq#1991's worktree-module-identity fix

**Verdict: CONFIRM.** Adversarial re-check of the diff shipped by wq#1991
(claude-foreman http://100.121.17.5:3000/lumen/claude-foreman/pulls/647,
squashed commit `28a701c`, already on main / this worktree's HEAD). The fix
does what it claims: no live module puts the primary checkout
(`/home/lumen/foreman`) on `sys.path` from a worktree run any more.

## The diff

8 files, +327/-17: three production `sys.path.insert` sites derived from
`__file__` instead of the literal (`despag/admission.py`,
`despag/backends/claude_max.py`, `despag/spike/app.py`), `fake_claude.py`'s
`GUARD_SH` default likewise, a pre-existing unrelated order-dependency bug
fixed in `test_liveness_clear.py` (sys.modules pop never restored — 7
downstream `test_token_injection` failures), and a new
`test_worktree_module_identity.py` (199 cases: parametrized AST lint over
every live-tree `.py` file + a live subprocess import-and-check).

## 1. Path arithmetic — CONFIRMED correct by inspection

Checked each `os.path.dirname(...)` chain against the file's actual nesting
depth: `despag/admission.py` (2 dirnames → repo root), `despag/spike/app.py`
and `despag/backends/claude_max.py` (3 dirnames each, both two directories
deep under `despag/`), `harness/tests/fake_claude.py` (2 dirnames → harness
dir). All land on the correct directory.

## 2. Adversarial re-check — CONFIRMED the new lock actually locks

Reinstated the original literal in `despag/admission.py` (`_GOVERNOR_DIR =
"/home/lumen/foreman/governor"` etc., no `__file__` derivation) and reran
`test_worktree_module_identity.py`: both the static lint
(`test_no_live_module_hardcodes_the_primary_tree_on_sys_path`) and the live
subprocess assert (`test_importing_despag_resolves_no_module_from_the_primary_tree`)
went red, citing the exact reinstated literal. Reverted; both tests pass
clean again (199/199). The regression lock is real, not decorative.

Also independently probed the static lint's own AST walker
(`_sys_path_literals`) with three shapes it does *not* claim to catch
(tuple-unpacking assignment, `os.path.join(...)` call argument, string
concatenation) — all three evade it silently. This is not a contradiction:
the docstring explicitly scopes the lint to the three shapes the original
bug actually took (inline / via-name / via-loop-variable) and pairs it with
the live subprocess assert as the authoritative check for anything the AST
can't see. Confirmed that live assert *does* catch a real reintroduction
regardless of shape (previous paragraph).

## 3. `ops/wq1991-worktree-module-identity/verify_5x.py` — ran fresh, 5/5 clean on the claim that matters

```
run 1: 62cdf8159d86a1aefe2975c49903be6b  FOREIGN_MODULES 0
run 2: 62cdf8159d86a1aefe2975c49903be6b  FOREIGN_MODULES 0
run 3: 4944fd73709ba5255125e416a375d50c  FOREIGN_MODULES 0
run 4: 4944fd73709ba5255125e416a375d50c  FOREIGN_MODULES 0
run 5: 4944fd73709ba5255125e416a375d50c  FOREIGN_MODULES 0
BYTE_IDENTICAL=False FOREIGN_FREE=True
```

`FOREIGN_MODULES 0` on every single repeat is the acceptance criterion this
ticket actually verifies — zero primary-tree leakage, reproduced
independently 5/5 times. `BYTE_IDENTICAL=False` looks like a regression
from the done-notes' claimed "5/5 identical at 4944fd73709ba525" (note:
runs 3-5 above hit that exact digest), but it isn't one — see below.

## Scope note — a related, already-tracked, out-of-scope issue (not a contradiction)

The extra failure that flipped runs 1-2 to runs 3-5
(`test_evidence_gate.py::test_stop_hook_reads_acceptance_from_run_dir_when_the_ticket_id_is_set`)
plus the two failures present in every run
(`test_fworker_integration.py::test_unrecoverable_worktree_failure_releases_claim_not_dangling`,
`test_wq1671_crash_before_charge.py::test_worktree_add_crash_writes_uncharged_harness_defect_record`)
are none of them touched by wq#1991's diff, and the done-notes state they
"reproduce identically with the whole diff stashed out — pre-existing, own
ticket." This matches ISSUE-LOG #420 (wq#2012, marked FIXED) exactly: both
tests point `--repo` at a plain `os.makedirs()`'d directory, and once
enough `chore/wq1-scratch-*` branches accumulate in the shared repo (now
108, up from 69 when #420 was filed — the prescribed fix was never actually
applied to either test file), `git worktree add` walks up and adopts
instead of crashing. Confirmed by file-scope (wq#1991 never touches these
three test files or `fworker.py`'s worktree-add path) and by the digest
match to the author's own claimed baseline. Filed wq#2034 to actually land
wq#2012's fix and sweep the accumulated cruft (1469 registered `git
worktree` entries fleet-wide before I cleaned up the 9 my own run added).
Logged as ISSUE-LOG #433. Does not contradict wq#1991.

## Conclusion

wq#1991's diff is scoped exactly to its stated problem, the path arithmetic
is correct, the regression lock demonstrably fires on reintroduction, and
the core acceptance criterion (zero primary-tree module leakage) reproduces
clean 5/5 on an independent run. The `BYTE_IDENTICAL=False` wrinkle traces
to unrelated, pre-existing, already-out-of-scope repo pollution, not to
this diff. **CONFIRM.**
