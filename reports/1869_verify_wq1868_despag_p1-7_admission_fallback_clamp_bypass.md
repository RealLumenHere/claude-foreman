# wq#1869 — Adversarial verify-pass of wq#1868 (despag P1-7: admission fallback clamp bypass)

**Verdict: CONFIRM.** PR #550 (`2b7106c`, landed on `main` as `d2e9cd6`) does what its
judgment section in `reports/1866_verify_wq1865_despag_p1-7_admission_fallback_clamp.md`
claims. Read the diff cold, ran its probe, then went further: mutation-tested the fix and
independently re-verified its two central factual claims against source.

## What was checked

- **Diff**: `git diff ef21e59 2b7106c` — `despag/admission.py`, `despag/tests/test_admission.py`,
  `despag/reports/p1-7_admission.md`, `evidence.json`,
  `reports/1866_verify_wq1865_despag_p1-7_admission_fallback_clamp.md`.
- **The fix** (`despag/admission.py:230-244`): on `allowed is None` (no pool has usable
  `percent`+`resets_at`), `allowed` now degrades to `min(cfg["min_allowed"],
  DESPAG_MAX_CLAUDE_CONCURRENT)` — 1 by default (`burn_rate.MIN_ALLOWED`) — instead of the
  wq#1865 fallback of `DESPAG_MAX_CLAUDE_CONCURRENT` (8) outright.
- **Probe** (`evidence.json` AC1 `cmd_run`): `python3 -m pytest despag/tests/test_admission.py -q`
  → **13 passed**. Full suite `python3 -m pytest despag/tests/ -q` → **134 passed**, matching
  the evidence note (133 pre-existing + 1 net-new test).
- **Dispatcher-mirror claim, verified independently**: read
  `dispatcher/foreman_dispatcher.py:1395-1424` directly. On `allowed_concurrent() -> None`
  (line 1411) the dispatcher sets `tight = True` and substitutes no concurrency number —
  confirmed line-for-line. `grep -rn DESPAG_MAX_CLAUDE_CONCURRENT dispatcher/` returns
  nothing: the constant genuinely does not appear in the dispatcher. The PR's claim that the
  wq#1865 "mirrors the dispatcher" comment was false, and that the fix's own mirror claim is
  true, both check out.
- **Test provenance**: `despag/tests/test_admission.py`'s `_mock_state` only stubs
  `foreman_usaged.load_floors/load_state/load_events/STOP_PATH` — `burn_rate.allowed_concurrent`
  and `pool_spend_rate` run unmocked, so the new tests exercise the real reserve arithmetic,
  not a stubbed shortcut.
- **Mutation test** (this verify-pass, not in the PR): reverted
  `despag/admission.py:243` to the pre-fix `allowed = DESPAG_MAX_CLAUDE_CONCURRENT`, reran
  `test_admission.py`. Both new tests failed as expected:
  `test_missing_resets_at_fails_closed_to_min_allowed` and
  `test_missing_resets_at_does_not_loosen_a_reserve_breached_account` (`assert 8 == 1` —
  the exact 1-vs-7/8 gap wq#1866 originally reported). Restored the fix; `git status` clean,
  full suite back to 134/134. The regression tests are non-vacuous.

## Not re-litigated

Did not re-review wq#1865/wq#1866/wq#1864's own findings (LIMIT wall, governor_core wiring,
reserve arithmetic outside the `allowed is None` branch) — out of scope for a verify-pass of
wq#1868's specific fix, and already covered by the prior confirmed judgment chain.

## Conclusion

wq#1868's fix is correct, its factual claims about the dispatcher hold up against source, its
regression tests are real and mutation-confirmed, and the full despag suite is green. No
contradiction found.
