# wq#2659 — verify-pass: wq#2651 macbench stale size+mtime cache fix (PR #1019)

**Verdict: CONTRADICT (narrow)** — the shipped fix is real and closes both defects it
targets, confirmed by ablation against the actual bug-reproduction path. But one of the
four "regression tests added, each ablation-verified to fail with its guard removed"
(kb/ISSUE-LOG.md row 755, wq#2651's own done-notes) is not ablation-verified:
`test_diff_is_counted_when_an_edit_kept_the_source_size_and_mtime`
(`tools/macbench/tests/test_agentic.py:83-87`) passes identically with the `diff_stats()`
scratch-index guard present or fully removed, because its helper can't actually construct
the same-mtime collision it's named for on this host. Production behavior is not at risk —
the real defect is still caught by a different, pre-existing test — but the specific claim
is false, and the suite carries one test that (per this fleet's own wq#2630 doctrine) cannot
fail.

PR: `lumen/claude-foreman` #1019, branch `fix/wq2651-judgment-wq-2246-macbench-verify-pass-co-a1`,
commits `e795378` (fix) + `3a6c6e2` (docs). Scope confirmed minimal: `tools/macbench/mb_agentic.py`,
`tools/macbench/tests/test_agentic.py`, `tools/macbench/README.md` only (41+62+9 lines).

## What was verified and holds up

- Offline suite: `python3 -m pytest tools/macbench/tests -q` → 46 passed, 0 failed, 3
  consecutive runs (no flake). Matches the "Suite 46/46, three consecutive runs" claim in
  kb/ISSUE-LOG.md row 755.
- `purge_bytecode()` + `PYTHONDONTWRITEBYTECODE=1` guard (the `.pyc` half of the original
  wq#2306/wq#2246 bug) is genuinely required: removing the `purge_bytecode(repo)` call and
  the env var from `run_test()` makes 3 of the 4 new tests fail as expected —
  `test_scoring_run_sees_a_fix_that_kept_the_source_size_and_mtime`,
  `test_a_models_own_self_check_cannot_poison_the_score`,
  `test_scoring_leaves_no_bytecode_behind`. Restored cleanly afterward (`git diff` empty,
  suite back to 46/46).
- `diff_stats()`'s scratch-index guard (the git half of the bug) is genuinely required, and
  IS caught by ablation — just not by the new test named for it. Removing the
  `GIT_INDEX_FILE` scratch-index block (falling back to a plain `git add -A` on the repo's
  real index) makes the **pre-existing, original bug-repro test**
  `test_task_fails_on_baseline_and_passes_with_reference_solution[e2_default]` fail exactly
  as it did before wq#2651 (`assert files > 0 and lines > 0` → `assert (0 > 0)`), using the
  suite's real on-disk fixture files (not synthetic ones). This is the same failure wq#2306
  originally reported. Restored cleanly afterward, suite back to 46/46.

## What's broken

`test_diff_is_counted_when_an_edit_kept_the_source_size_and_mtime` is not ablation-verified.
Reproduction:

1. Ablate only `diff_stats()`'s scratch-index block (replace the `GIT_INDEX_FILE`-scoped
   `git add -A` + `git diff --cached` with the plain, pre-fix two-liner operating on the
   repo's real index).
2. Run all 4 new tests: `pytest -k "test_scoring_run_sees_a_fix_that_kept_the_source_size_and_mtime or test_a_models_own_self_check_cannot_poison_the_score or test_diff_is_counted_when_an_edit_kept_the_source_size_and_mtime or test_scoring_leaves_no_bytecode_behind"` → **4 passed**. The
   diff_stats-guard test does not fail.
3. Root cause: the shared helper `_apply_fix_preserving_size_and_mtime()` calls
   `os.utime(src, (st.st_atime, st.st_mtime))` to roll the mtime back after a rewrite.
   `st_mtime` is a Python `float`; round-tripping a nanosecond-resolution timestamp through
   it loses precision. Measured directly on this host:
   `mtime_ns` before rewrite `1790334161119872799`, after `os.utime()` restore
   `1790334161119872808` — a 9ns drift, never an exact match. `ctime` also necessarily
   differs (any write updates it; it cannot be restored from userspace). This git build's
   index stat-match check is nanosecond-precision, so the "same size+mtime" collision this
   test's docstring claims to construct never actually happens here — git already
   re-reads and hashes the file on the plain, unscratched index path, independent of
   whether the scratch-index fix exists.

This matches this fleet's own acceptance-probe doctrine (wq#2630, quoted in the BUILD-worker
prompt verbatim): *"an acceptance probe must be able to fail... a probe drawn only from the
population the fix was designed around confirms and never falsifies."* This specific probe
cannot fail on this host under any code state.

## Why this doesn't threaten the shipped fix

The `diff_stats()` scratch-index guard is still real, still needed in production, and is
still under regression protection — just from `test_task_fails_on_baseline_and_passes_with_reference_solution[e2_default]`,
not from the test written to demonstrate it. Nothing about the shipped `mb_agentic.py`
behavior is wrong. The defect is entirely in the accuracy of the
"4 regression tests... each ablation-verified" claim and in the redundant test's fragility
(it would silently stop meaning anything if `e2_default` were ever renamed/removed, since
nothing else guards `diff_stats()` against a real regression).

## Recommendation (not applied — verify-pass, no re-implementation)

Escalating per archetype 8 rules. Judgment call for the opus-5@high pass: either delete
`test_diff_is_counted_when_an_edit_kept_the_source_size_and_mtime` as redundant with
`e2_default`, or make it actually collide (e.g. build both baseline and "fixed" content via
a single `os.replace`/hardlink trick, or force-write matching `stat` via a lower-level
syscall) so it can fail on this host too. Low severity — does not block wq#2651 staying
merged.
