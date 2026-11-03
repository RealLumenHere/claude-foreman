# wq#4832 escalated judgment: wq#4828 / PR #2591 (limpet atomic config transaction wrapper)

Judge: opus-5@high. Judgment only: no patch, no PR comment, no changes to `atomic_apply.py` or its tests. Date: 2026-11-03.
Subject: `limpet/atomic_apply.py` as merged to main in b18d22bc (PR #2591). The worktree copy is identical to main
(`git diff --stat main -- limpet/atomic_apply.py limpet/test_limpet_atomic_apply.py` is empty;
sha256 `fc8a4315ccdf8ce248fba53dba9b7e8b441fd61d00ce7c53a832310d1fcc5135`).

## Claim under judgment

Stated so it can be proven false:

> `run_transaction()` honors "on ANY failure: reverts ALL files from snapshot". No code path exists where one or
> more files in a multi-file transaction end up mutated on disk, the transaction does not commit, and no rollback
> runs.

The claim is false if any reachable input leaves an earlier target with its new bytes on disk while
`run_transaction` exits without committing and without running the restore loop at `atomic_apply.py:114-118`.

## Evidence

### Control flow of `run_transaction` (`atomic_apply.py:57-128`)

| Lines | Step | Exceptions handled |
|---|---|---|
| 77-78 | create staging dir | none |
| 83-93 | snapshot loop: `target.exists()`, `read_bytes()`, `snap_path.write_bytes()` per file | none |
| 95-98 | apply loop: `target.parent.mkdir(parents=True, exist_ok=True)`, `target.write_bytes(...)` per file | **none** |
| 100-105 | health check `runner(...)` | `subprocess.TimeoutExpired`, `OSError` only |
| 107-112 | commit: `rmtree(stage)`, `on_commit(result)` | none |
| 114-118 | restore loop (the only rollback) | none |
| 120-121 | `rollback_cmd` via `runner` | none |

The only rollback is lines 114-118, and it runs only when control reaches line 114 with `code != 0`. An exception
raised at lines 97-98 for file *k* leaves files *0..k-1* already overwritten (line 98 ran for them). Nothing catches
it, so it skips the health check, the restore loop and the `return`. The snapshot-to-target mapping (`snapshots`,
line 83) exists only as a local variable, so it is lost when the exception propagates. The staging dir survives,
but its files are named `<sha256>.snap` (line 89) with nothing recording which target each one belongs to.

### Verifier probe v2, re-run by the judge

`python3 -I /tmp/fworker-wq4832/probe/probe_partial_apply2.py`:

```
bad.exists() before run: False
exception propagated: NotADirectoryError(20, 'Not a directory')
good.yaml now reads: b'new: good\n'
CONFIRMED: half-applied state -- good.yaml mutated, exception raised, no rollback, no TransactionResult
EXIT=1
```

### Verifier probe v1, re-run for contrast

`python3 -I /tmp/fworker-wq4832/probe/probe_partial_apply.py`:

```
exception propagated: IsADirectoryError(21, 'Is a directory')
good.yaml now reads: b'old: good\n'
NOT CONFIRMED: transaction stayed atomic
```

v1's second target is an existing directory. `target.exists()` is True, so `target.read_bytes()` at line 87 raises
inside the **snapshot** loop, before line 98 has run for any file. Nothing is mutated. v1 shows the module does not
handle an exception. It does not show a half-applied state. v2's second target does not exist (an intermediate path
component is a regular file), so the snapshot loop records `(bad, None)` at line 93 without raising. The failure is
deferred to line 97 in the **apply** loop, after line 98 has already written `good.yaml`. Only v2 disproves the claim.

### Independent judge probes (inline `python3 -I -`, tempdirs only, uid 1000)

| Case | Setup | Outcome |
|---|---|---|
| A | file 1 `litellm_config.yaml` (exists, "old"), file 2 `systemd/foo.service` in an existing `chmod 0555` dir, runner returns 0 | `PermissionError(13)` escapes from line 98; `litellm_config.yaml` = `b'new\n'`; staging dir left with one anonymous `01d09d19...snap` |
| B | one file, runner raises `ValueError` (not `TimeoutExpired`/`OSError`) | `ValueError` escapes from line 101; file = `b'new\n'`, never restored |
| C | one file, `health_check="exit 1"`, `rollback_cmd="sleep 5"`, `health_timeout=1`, default shell runner | file restored to `b'old\n'`, then `TimeoutExpired` escapes from line 121; no `TransactionResult`, failure not logged |

Case A is the strongest. It uses no contrived path trick: a non-root caller doing the ticket's own pairing of a
litellm config swap with a systemd unit install into a directory it cannot write. Case B shows the same gap through
a second entry point, the health check, for any exception outside the two caught types. Case C is a narrower gap: the
files do get reverted, but the "restart affected services" and "logs the failure" parts of criterion #4 are skipped.

### Author's tests

`python3 -m pytest limpet/test_limpet_atomic_apply.py -q` gives `7 passed in 0.04s`. Every failure path in the suite
goes through the health-check return code or a `TimeoutExpired` from the runner. The only multi-file test
(`test_multi_file_transaction_rolls_back_all_files_together`, test file lines 106-120) fails through
`runner=lambda cmd, t: (1, "boom")`, after both writes have succeeded. No test raises during the snapshot or apply
loops. This is a coverage gap, not a regression.

## Verdict

**CONTRADICT.** The claim is false. If a write fails partway through the apply loop (`atomic_apply.py:95-98`), every
earlier target keeps its new bytes. The exception propagates out of `run_transaction` with no rollback, no
`rollback_cmd`, no `TransactionResult` and no log line. I reproduced it with the verifier's probe v2
(`NotADirectoryError`) and with a more realistic `PermissionError` case of my own. This contradicts acceptance
criterion #4 ("On ANY failure: reverts ALL files from snapshot") and the module's own docstring at lines 3-6.

## Reasoning

**Scope: does "ANY failure" cover write-time failures?** Yes. Criterion #4 says "ANY", and criteria 2 and 3 list
apply and health check as separate steps. A reading where "failure" means "health check returns non-zero" would make
"ANY" redundant. The ticket also names the targets: litellm_config.yaml, fleet-exec-policy.yaml, compose files, and
systemd units. These are real paths with mixed ownership (user-owned config next to root-owned `/etc/systemd/system`),
on real disks that can fill up. A transaction spanning them can fail between the first and last write. Even if
criterion #4 were ambiguous, the docstring removes the ambiguity: "either fully applies or fully reverts -- never a
half-applied state ... left live" (lines 3-6) has no condition attached. The module promises more than the code
delivers.

**Counter-argument 1: contrived edge case.** The verifier's specific trigger (a regular file used as a directory
component) is unusual. Case A is not: a non-root fleet agent writing a systemd unit gets `EACCES`, and that is the
most likely first-run mistake for the ticket's own use case. `ENOSPC` and `EROFS` follow the same path. The trigger
details do not matter. Any exception from lines 97-98 after the first iteration produces the bug.

**Counter-argument 2: crashing loudly is safer than succeeding silently.** I agree that raising beats returning
`committed=True`. But the real comparison is between raising and rolling back, then returning `committed=False`. A
loud crash still leaves state that is harmful in practice:

- File 1 (e.g. `litellm_config.yaml`) now holds bytes that never passed the health check. Nothing has reloaded it
  yet, so the outage is latent. The next unrelated `docker compose restart` loads an unvalidated config. That is the
  "new config, old container" split the ticket cites, with the trigger moved to later.
- Cleanup is manual. The old bytes still exist, but only as `<stage>/<sha256>.snap`, with nothing mapping them back to
  a path. For a single-file transaction that is recoverable by inspection. For N files it means matching hashes by
  hand. The ticket describes current practice as "rollback is manual and partial", and this failure mode is exactly
  that.
- No episode or failure log is written (criterion #4, "logs the failure"). The fleet records nothing about the
  half-apply unless the caller catches and logs it.

So in this failure class the wrapper behaves like the precheck-plus-manual-backup process it was meant to replace, and
it says otherwise in its docstring. That is a real gap against the acceptance criteria, not a style point.

**What the PR does get right.** The health-check-failure path (lines 107-128) is correct and tested. It restores
existing files byte-exact, deletes newly created ones, and runs `rollback_cmd`, including across multiple files. That
covers the most common failure (bad config that parses but breaks the service). The defect is limited to exceptions
raised before or around that path, but it is reachable on the ticket's own use cases.

**Not probed, noted for the fix.** `Path.write_bytes` truncates in place, so an `ENOSPC` partway through line 98 can
leave the *current* file truncated as well. The restore loop (lines 114-118) and `on_commit` (line 111) are also
unguarded. An exception in the restore loop would itself leave a partial revert. `on_commit` raising after `rmtree` is
less serious, because the files are committed and healthy.

## Recommendation

Accept the current merge for its health-check path, and open a forward-fix ticket against `limpet/atomic_apply.py`.
Do not revert PR #2591. The fix should cover:

1. **Wrap apply and health check together.** Put lines 95-105 in one `try`, and send any `BaseException` from the
   apply loop or the runner into the same restore-and-`rollback_cmd` path. Return `TransactionResult(False, reason=...)`
   with the exception class and the failing path, or re-raise after rolling back if the house prefers exceptions.
   Either way, nothing written may survive unreverted.
2. **Validate before the first write.** Move `target.parent.mkdir(...)` and a writability check into a pre-apply pass
   so that predictable failures (`EACCES`, `ENOTDIR`) abort before any file changes.
3. **Atomic per-file writes.** Write to a temp file in the same directory, `fsync`, then `os.replace`. A failed write
   can then never truncate a live file.
4. **Durable manifest.** Write `<stage>/manifest.json` (target path, snap name, existed-before flag) before the first
   apply, so that a crash, kill or power loss leaves a recoverable map rather than anonymous `.snap` files.
5. **Guard the rollback itself.** Make the restore loop best-effort per file and collect errors. Catch
   `TimeoutExpired` / `OSError` from `rollback_cmd` (line 121) and put them in `reason` instead of raising.
6. **Tests.** Add a regression test for probe v2 / case A: a 2-file transaction where file 2 fails to write must leave
   file 1 byte-identical to its original. Add one for case B (runner raises `ValueError`) and one for case C
   (`rollback_cmd` timeout returns a result instead of raising).

## Note on process

The `guard.sh` read hook blocked full-file reads of `atomic_apply.py` and the wq#4732 format reference as "already
read this session", probably from the parent verifier's reads under a shared session ID. This judge used ranged reads
(offset 1), which returned both files in full, and cites line numbers from those reads.

The caller asked for this report at `/home/lumen/foreman/reports/`. `guard.sh` blocks Write in the primary tree
(wq#1719/wq#900) and points to the worker's own worktree. So this report was written to the same repo-relative path
in `/home/lumen/worktrees/wq4832-a1/reports/` (the same repo, `git-common-dir` = `/home/lumen/foreman/.git`) and left
uncommitted for the caller to land with `lumen-pr`.

All probes ran in `tempfile.mkdtemp` dirs. Nothing in `limpet/`, the PR or `/home/lumen/foreman` was modified. The
`lumen_busy` key (`phase=escalating_to_opus_judgment`, pid 3574158) belongs to the parent verifier and was left for
it to clear.
