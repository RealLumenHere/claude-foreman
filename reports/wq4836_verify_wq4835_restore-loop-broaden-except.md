# wq#4836 verify: wq#4835 / PR #2597 (limpet atomic-apply restore-loop except broadening)

Verifier: claude-sonnet-5@high (fresh instance). Author: claude-sonnet-5. No escalation — no contradiction found.
Subject: `limpet/atomic_apply.py` and `limpet/test_limpet_atomic_apply.py` at commit `8d5963c2`
(worktree `/home/lumen/worktrees/wq4836-a1`, identical to PR #2597 / main at that commit — `git log main..HEAD` empty).

## Claim under verification

wq#4835 was opened against the exact scope recommended by the wq#4834 opus-5@high judgment
(`reports/wq4834_judgment_wq4833_restore-loop-narrow-except.md`), which had CONFIRMED that
`atomic_apply.py:176`'s `except OSError as exc:` let any non-`OSError` raised during restore
(e.g. `ValueError` from a NUL byte in a path, or a patched `RuntimeError`/`MemoryError`) escape
`run_transaction` uncaught — skipping the rest of the restore loop, `rollback_cmd`, and the
`TransactionResult` return.

The diff changes one line: `atomic_apply.py:176` `except OSError` -> `except Exception`. The claim
is false if any reachable input still lets an exception escape the restore loop, or still stops
remaining snapshots/`rollback_cmd` from running.

## Evidence

### Code change
`limpet/atomic_apply.py:176` now reads `except Exception as exc:` (previously `except OSError`),
matching the `rollback_cmd` block two lines below it and the module's own comment at lines 167-168
("must never escape uncaught"). This is exactly recommendation 1 from the wq4834 judgment.

### Author's tests, replayed
`python3 -m pytest limpet/test_limpet_atomic_apply.py -v` (run from `/home/lumen/worktrees/wq4836-a1/limpet`):
12 passed (was 10 before wq#4835). The two new tests map 1:1 onto the judgment's recommendations 2 and 3:

- `test_non_oserror_during_restore_is_best_effort_not_raised` — two files, both applied, restore of
  file `a` raises `RuntimeError` on its first restore call only (not every call), health check fails,
  `rollback_cmd` set. Asserts: `committed is False`, `b` reads its original bytes, `rollback_calls ==
  ["rollback-service"]`, and `"restore"` plus `a`'s path appear in `reason`. This is recommendation 2
  (case B2 from the judgment) verbatim.
- `test_nul_byte_path_during_restore_is_best_effort_not_raised` — unpatched trigger: targets
  `[good.yaml, "bad-\x00-name.yaml"]`, `rollback_cmd` set. Asserts `committed is False`, `good.yaml`
  keeps its original bytes, `rollback_calls == ["rollback-service"]`. This is recommendation 3
  (case C) verbatim, and matches the judgment's own finding that `Path.unlink()` on an embedded-NUL
  path raises `ValueError`, not `OSError` — reconfirmed independently below.
- `test_second_file_write_failure_rolls_back_first_file_unharmed` was tightened per recommendation 4:
  `flaky_write` now raises only on the first restore call for `b`, so `b`'s restore actually
  succeeds on the retry, and a new assertion `"restore" not in res.reason` closes the gap the
  judgment flagged (the old test passed regardless of whether `b`'s restore worked).

Full suite: `python3 -m pytest limpet/` — 888 passed, no regressions.

### Independent reproduction of the embedded-NUL exception type
```
>>> Path("/tmp/.../bad-\x00-name.yaml").unlink(missing_ok=True)
ValueError: unlink: embedded null character in path
```
Confirms the fix addresses a real, non-`OSError` exception class reachable with no monkeypatching.

### Verifier's own adversarial probe (beyond the author's tests)
`/tmp/fworker-wq4836/probe_wq4836.py`: three files, all applied, health check fails, `rollback_cmd`
set. Restore of `a` raises `ValueError`, restore of `b` raises `MemoryError` (two different
non-`OSError` types in the *same* transaction, not just one), restore of `c` succeeds. Result:

- `run_transaction` returns normally, does not raise.
- All three snapshots are attempted (`restore_calls` has all three paths, in order) — a failure on
  `a` does not stop `b` or `c` from being attempted, and a second failure on `b` does not stop `c`.
- `c` is correctly restored to `c-old` despite two prior restore failures.
- `rollback_cmd` still runs (`rollback_calls == ["rollback-service"]`).
- `reason` contains both failure messages ("restore .../a.yaml failed: embedded null simulation;
  restore .../b.yaml failed: oom simulation").

This is a harder case than either new author test (two concurrent non-`OSError` failures of
different types, not one) and the fix holds under it.

### Scope check: nothing else changed
`git show 8d5963c2 --stat`: only `atomic_apply.py` (1 line) and the test file. No change to the
apply-loop `try` (lines 148-158, already broad per wq#4833), the `rollback_cmd` except (line 182,
already `Exception`), or the snapshot phase (explicitly out of scope per wq4834's judgment). `python3
-c` confirms `KeyboardInterrupt`/`SystemExit` are not `Exception` subclasses, so they still propagate
through the broadened clause — consistent with the module's existing house choice, not a regression.

## Verdict

**CONFIRM.** wq#4835's one-line change (`OSError` -> `Exception` at `atomic_apply.py:176`) closes
exactly the gap the wq4834 judgment identified, its two new tests faithfully reproduce the judgment's
case B2 and case C, and the tightened existing test closes the judgment's note that the old version
could not distinguish a working restore from a failed one. My own probe, using two different
non-`OSError` exception types in a single three-file transaction, found no case where an exception
still escapes `run_transaction`, where a remaining snapshot is skipped, or where `rollback_cmd` is
skipped. No contradiction — no escalation to opus-5@high.

## Note on process

Probe script and its temp dirs are under `/tmp/fworker-wq4836/`, outside the worktree and outside
`/home/lumen/foreman`, and were run under `python3 -I`. Nothing in `limpet/` was modified by this
verify pass. `gitea` MCP pull-request read tool was requested but not granted in this headless
session; verification instead used the local worktree (git-identical to the PR per `git log
main..HEAD`) plus the full local test suite and an independent probe, which is sufficient evidence
for this diff's scope.
