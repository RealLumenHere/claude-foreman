# wq#5343 verify of wq#5336 (PR 2794, foreman bd1f8588)

Verdict: **CONFIRM**. Verifier: claude-sonnet-5-5@high lane, fresh instance. Diff read cold.

## What wq#5336 shipped
Two test files only; no bundle code changed.

- `ops/tests/test_wq5269_minerva_guard_intended_off.py`: new `verify_block()` / `ungated_timer_asserts()`.
  These strip the `if [ "$INTENDED_ON" = 1 ]; then ... fi` branch out of the `--verify` block before
  checking for `venabled|vactive|vtimer_scheduled lumen-minerva-guard.timer`. Also adds
  `test_static_check_flags_an_ungated_assert`, a falsifiability test.
- `ops/tests/test_wq1609_scan_verify.py`: 3 stale tests replaced. The old ones asserted the
  timer-state reads that c866b57d / wq#5255 removed. The replacements are
  `test_does_not_read_timer_state` and `test_intended_off_timer_is_not_a_miss`, plus
  `test_old_asserts_would_have_failed_under_same_stub`, which runs a mutated copy of the bundle
  through `_run(install_text=...)`.

## Replayed
- `python3 -m pytest -q ops/tests/test_wq5269_minerva_guard_intended_off.py ops/tests/test_wq1609_scan_verify.py`: 10 passed.
- `python3 -m pytest -q /home/lumen/foreman/ops/tests`: **454 passed**, 2 subtests passed (115 s). That is the ticket goal.
- Adversarial probe `scratch/wq5343_probe.py`, rev-pinned via `git show bd1f8588:...`: 9/9 as expected.
  0. The pre-fix unscoped regex on the pinned main `install.sh` flags all 3 asserts. The reported red reproduces.
  1. The unmodified bundle has no ungated asserts.
  2. An assert after the gate's `fi` is flagged.
  3. An assert inside a different `if [ "$OTHER" = 1 ]` is flagged. Only the INTENDED_ON gate is exempt.
  4. An assert before the gate is flagged.
  5. A nested `if` inside the gate makes the lazy match stop at the inner `fi`. The remaining gated
     asserts are then flagged: a false positive, which is the safe direction.
  6. **Blind spot (minor, non-blocking).** Suppose someone adds an `else` branch to the INTENDED_ON
     gate. That branch runs exactly when the timer is intended-off, but the strip regex removes it
     along with the gate, so an assert there is **not** flagged statically.
  6b. The same suite's behavioural test `test_disabled_inactive_timer_raises_no_miss`, through
      `run_verify`, does catch that `else`-branch assert (`MISS not enabled`). The suite as a whole
      stays falsifiable.

## Findings (non-blocking)
1. The static check's `else`-branch blind spot (probe case 6). The behavioural test covers it, so no
   rework is needed. A hardening option: strip only up to an `else`/`fi` at the gate's indent.
2. The commit message carries a `Co-authored-by: Lumen Industries` trailer, which breaks the house
   rule (`Assisted-by:` only, wq#4796). The trailer looks injected by the commit path rather than
   by the worker, since it names the house identity and not Claude. It is cosmetic and on main already.
3. `test_intended_off_timer_is_not_a_miss` (wq1609) does not assert a return code. It checks only
   that no timer MISS appears, which is right for its purpose, because unrelated live-file checks
   can legitimately fail.

## Liveness
`bash /home/lumen/foreman/ops/wq3147-minerva-guard/install.sh --verify` was refused by this
worker's Bash allowlist ("not in the permission allow list"), so the live verify was not replayed
here. The diff touches no bundle file, so live state is unaffected by it. The `--verify` path itself
was exercised under systemctl stubs by `run_verify` (all green).
