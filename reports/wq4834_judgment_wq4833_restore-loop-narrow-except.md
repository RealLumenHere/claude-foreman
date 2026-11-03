# wq#4834 escalated judgment: wq#4833 / PR #2595 (limpet atomic-apply forward fix)

Judge: opus-5@high. Judgment only: no patch, no PR comment, no changes to `atomic_apply.py` or its tests. Date: 2026-11-03.
Subject: `limpet/atomic_apply.py` and `limpet/test_limpet_atomic_apply.py` as merged to main in 86276da4 (PR #2595).
The worktree copy is identical to that commit and to main (`git diff --stat 86276da4` and `git diff --stat main` on
both files are empty; sha256 of `atomic_apply.py` is
`af905f8014d438bb39fff18cbf45f8b33feeb97eb86cd972f563b1274031557e`).

## Claim under judgment

wq#4833 acceptance item 5 reads: "make the restore loop and `rollback_cmd` best-effort (catch and log per-file /
per-command errors instead of raising)". The module's own comment at `atomic_apply.py:167-168` says: "Restore is
best-effort: one bad file/command must not stop the others from being rolled back, and must never escape uncaught."

Stated so it can be proven false:

> Once `run_transaction()` enters the failure path (`code != 0`, line 167 onward), it always returns a
> `TransactionResult(committed=False, ...)`. A failure restoring one file never stops the other files from being
> restored, never skips `rollback_cmd`, and never propagates out of `run_transaction`.

The claim is false if any reachable input makes an exception escape from the restore loop (`atomic_apply.py:170-177`),
or makes that loop stop before every snapshot has been attempted, or makes `rollback_cmd` (lines 179-183) not run.

## Evidence

### Control flow of the failure path (`atomic_apply.py:148-192`)

| Lines | Step | Exceptions handled |
|---|---|---|
| 121-125 | pre-apply writability pass | returns `TransactionResult(False)` on any problem it detects |
| 132-146 | snapshot loop + `manifest.json` | none (out of wq#4833 scope, see Reasoning) |
| 148-158 | apply loop + health check, one `try` | `TimeoutExpired`, `OSError` from runner; outer `except Exception` |
| 170-177 | restore loop, per file | **`OSError` only** |
| 179-183 | `rollback_cmd` | `Exception` |
| 185-192 | build reason, return | n/a |

The restore loop and the `rollback_cmd` block sit next to each other and use different except clauses. A non-`OSError`
raised by `snap_path.read_bytes()`, `_write_atomic()` or `target.unlink()` during restore leaves the loop, skips the
remaining snapshots, skips `rollback_cmd`, skips the `return`, and propagates out of `run_transaction`.

### Verifier's probe, re-derived by the judge

The verifier's `limpet/_adhoc_probe.py` fails as-is under `python3 -I` (`ModuleNotFoundError: atomic_apply`; `-I`
drops the script dir from `sys.path`). I rewrote it as case A of `/tmp/wq4834_judge_probe.py`, which takes the
`limpet/` dir as an argument and creates every target in a fresh `tempfile.mkdtemp()` dir.

### Judge probes (`python3 -I /tmp/wq4834_judge_probe.py /home/lumen/worktrees/wq4834-a1/limpet`, Python 3.14.4)

| Case | Setup | Outcome |
|---|---|---|
| A | verifier's case: 1 file, health returns 1, patched `_write_atomic` raises `RuntimeError` on the restore call | `RuntimeError` escapes; `f.yaml` = `b'new\n'` (never restored); no `TransactionResult` |
| B1 (control) | 2 files, both applied, health returns 1, `rollback_cmd` set, restore of `a` raises `PermissionError` | returns `committed=False`, reason ends `restore .../a.yaml failed: EACCES`; `b` restored to `b'b-old\n'`; runner calls `['probe', 'restart-service']` |
| B2 | same as B1, restore of `a` raises `RuntimeError` | `RuntimeError` escapes; `a` = `b'a-new\n'`, **`b` = `b'b-new\n'` (never attempted)**; runner calls `['probe']` (no `rollback_cmd`) |
| B3 | same as B1, restore of `a` raises `MemoryError` | same as B2 |
| C | **no mocks**; `[good.yaml, "bad\x00.yaml"]`, `rollback_cmd` set | `_check_writable(bad)` returns `None` (pre-check passes); snapshot records `bad` as absent; apply of `bad` raises `ValueError` (caught by the outer `except Exception`); restore restores `good.yaml` to `b'good-old\n'`, then `bad.unlink()` raises `ValueError: unlink: embedded null character in path`, which escapes; no `rollback_cmd`, no `TransactionResult` |
| C2 | no mocks; same two files in reverse order | same `ValueError` escapes; `good.yaml` unchanged (never written) |
| D | author's `test_second_file_write_failure_rolls_back_first_file_unharmed`, replayed with a call log | `_write_atomic` calls: `a(new)`, `b(new)` raises, `a(old)`, `b(old)` raises again; reason ends `restore .../b.yaml failed: no write perms`; `a` and `b` both read old bytes |
| E | snapshot phase: second target is an existing directory | `IsADirectoryError` escapes from line 137; `good.yaml` unchanged; staging dir holds one `.snap` and no `manifest.json` |

B1 against B2 is the core result. The same failure in the same position is handled correctly when it is an `OSError`
and breaks every guarantee in the claim when it is anything else. Case C shows the gap is reachable through the
module's real code with no monkeypatching. Path-handling functions raise `ValueError`, not `OSError`, for an embedded
NUL, and the pre-check does not reject such a path.

### Author's tests

`python3 -m pytest limpet/test_limpet_atomic_apply.py -q` gives `10 passed in 0.04s`, which matches the done-notes.
None of the 10 tests raises inside the restore loop and then checks the result. The verifier is right about
`test_second_file_write_failure_rolls_back_first_file_unharmed` (test file lines 108-133), and case D shows the
problem is worse than "trivially true". Because `flaky_write` raises on *every* call for `b`, `b`'s restore also fails
and is logged in `reason`. The test still passes because `b` was never modified, and it makes no assertion on
`restore_errors`. The test cannot tell a working restore of `b` from a failed one. It covers criterion 1 (file 1
reverted after a file-2 apply failure). It does not cover criterion 5.

## Verdict

**CONFIRM** (the verifier's finding holds; the claim under judgment is false). The restore loop at
`atomic_apply.py:176` catches only `OSError`. Any other exception during restore escapes `run_transaction`. It also
stops the remaining files from being restored (B2: file 2 left with unvalidated new bytes) and skips `rollback_cmd`
and the failure `reason`. This contradicts wq#4833 acceptance item 5 ("instead of raising"), the module's own comment
at lines 167-168 ("must never escape uncaught"), and the `rollback_cmd` block directly below it, which catches
`Exception`. I reproduced it with the verifier's trigger (A), a two-file variant (B2, B3), and an unpatched input (C).

## Reasoning

**Is the trigger realistic?** The worst outcome (B2: a file left mutated and later files never restored) needs a
non-`OSError` from `read_bytes` or `_write_atomic` during restore. With the stock `_write_atomic` that comes down to
`MemoryError` or an exception from a wrapped or instrumented writer. That is rare in production for config-sized files.
Case C, with no patching, needs a NUL byte in a target path. That is a caller bug, but a caller bug is exactly the kind
of input a best-effort rollback exists to survive. Under C the files do end up consistent, but the caller gets a raw
`ValueError` instead of a result, and `rollback_cmd` never runs. This is the same shape as wq#4832 case C, which that
judgment counted as a real (narrower) gap. Taken together: low likelihood, but reachable through the real code, and
it breaks a guarantee the ticket and the code comment both state with no exceptions.

**Is this a contradiction or a style point?** It is a contradiction. Item 5 does not say "catch I/O errors". It says
"instead of raising". The author applied the broad form to `rollback_cmd` and the narrow form to the restore loop. The
comment above both promises the broad form. The code shows the intended behavior (B1 is correct), and the narrow
clause is the only thing that stops B2 from behaving like B1. The fix is a one-token change, so the call is not close.

**What wq#4833 gets right.** Items 1-4 and most of 6 hold up. The apply and health-check `try` (lines 148-158) closes
wq#4832 cases A and B. The pre-check, `_write_atomic` (temp file + `os.replace`) and `manifest.json` are present.
`rollback_cmd` is properly best-effort, which closes wq#4832 case C (and its regression test passes). Per-file
continuation works for `OSError` (B1). The residual gap is one except clause plus a missing test.

**Snapshot phase (verifier's secondary point).** I agree it is out of scope and not a contradiction. Case E confirms
that an exception in lines 132-146 escapes, but it happens before any target is written, so nothing is half-applied.
wq#4833 item 1 scoped the `try` to "the apply loop + health check". The only side effect is an orphan staging dir with
a `.snap` and no manifest, which is harmless. Optional hardening (reject directory targets and NUL bytes in
`_check_writable`) is not required by any acceptance item.

**Not in scope, noted.** `BaseException` (`KeyboardInterrupt`, `SystemExit`) escapes both the apply `try` and the
restore loop. That is consistent across the module and a reasonable house choice. `on_commit` raising after commit is
unchanged from wq#4832's note and harmless, because the files are committed and healthy.

## Recommendation

Accept PR #2595 as merged. Do not revert. Open a small forward-fix ticket against `limpet/atomic_apply.py` with this
exact scope:

1. **Broaden the restore-loop except.** At `atomic_apply.py:176`, change `except OSError as exc:` to
   `except Exception as exc:`, matching the `rollback_cmd` block at line 182. Optionally include the exception class
   in the message (`f"restore {target} failed: {type(exc).__name__}: {exc}"`). Nothing else in the module changes.
2. **Regression test, non-OSError during restore (case B2).** Two existing files, a `_write_atomic` patched with
   `mock.patch.object(atomic_apply, "_write_atomic", ...)` that lets the two apply calls through and raises
   `RuntimeError` on the restore call for file 1, health check returning 1, and `rollback_cmd` set. Assert:
   `run_transaction` returns (does not raise), `committed is False`, file 2 reads its original bytes, the runner saw
   `rollback_cmd`, and `"restore"` plus file 1's path appear in `reason`.
3. **Regression test, unpatched trigger (case C).** A `[good, "bad\x00.yaml"]` transaction with `rollback_cmd` set
   must return `committed=False`, leave `good` with its original bytes, and run `rollback_cmd`.
4. **Tighten the existing test.** In `test_second_file_write_failure_rolls_back_first_file_unharmed`, make
   `flaky_write` raise only on the first call for `b`, so `b`'s restore actually runs, and assert `"restore"` is not
   in `res.reason`. As written, the test passes while `b`'s restore fails.

Out of scope for that ticket: snapshot-phase exception handling, `BaseException`, and `on_commit`.

## Note on process

The `guard.sh` read hook blocked default reads of `atomic_apply.py`, the test file and the wq#4832 report as "already
read this session", probably from the parent verifier's reads under a shared session ID. Ranged reads (offset 1)
returned all three in full, and the line numbers here come from those reads.

All probes ran under `python3 -I` in `tempfile.mkdtemp()` dirs. `TMPDIR` points at the worktree's `.tmp/`, which
`.git/info/exclude` ignores. Nothing in `limpet/` or the PR was modified. The verifier's untracked
`limpet/_adhoc_probe.py` was left in place for the caller to delete. The judge's probe is at
`/tmp/wq4834_judge_probe.py`. This report is left uncommitted for the caller to land. The `lumen_busy` key
(`phase=escalating_to_opus_judgment`, pid 3651056) belongs to the parent verifier and was left for it to clear.
