# wq#4505 — judgment on wq#4494 / PR#2365 (reconcile_orphaned_worktrees.py)

**Verdict: CONTRADICT. CONFIRMED defect, medium severity — audit-trail loss on
first `rmtree` failure, not data loss. Escalated to opus-5@high, upheld.**

wq#4494's deliverable is sound on its core promise: orphan detection
(`is_orphaned`), ticket-status gating (`classify`, fail-closed on any
unresolved/non-done ticket), and the archive-then-verify-then-delete sequence
(`archive_dir` + its byte-compare round-trip) all hold up — no checkout
directory's *contents* are ever at risk, because deletion only happens after a
tar+zstd archive has been test-extracted and compared file-for-file against
the source. 10 passing tests cover that surface. But the tool's own fourth
stated guarantee — "Appends one JSON line per run to `--log` ... with every
path's action and reason" (docstring, step 4) — does not hold under the one
failure mode none of those 10 tests exercise.

## The defect

`ops/wq4494-reconcile-orphaned-worktrees/reconcile_orphaned_worktrees.py`,
`run()` lines 264–290: for each eligible directory, `shutil.rmtree(path)`
(line 282) sits inside the per-directory loop with **no try/except**.
`append_log()` (line 319) is called exactly once, in `main()`, only *after*
`run()` returns.

If `shutil.rmtree` raises an `OSError` on any directory partway through a
batch — permission bits on an abandoned checkout, a stray symlink, an NFS
hiccup — that exception propagates straight out of `run()` and `main()`,
crashing the process before line 319 ever runs. The entire run's JSONL audit
trail is lost, **including the archive+delete record for every directory
already successfully archived, verified, and deleted earlier in that same
run.** The data itself is safe (it's sitting in `archive_root`, already
byte-verified); only the paper trail of what happened is gone. Separately, a
partial `rmtree` that gets far enough to delete a worktree's `.git` pointer
file before raising will cause a re-run's `is_orphaned()` to misclassify the
half-deleted directory as "not a linked-worktree pointer at all" (`gitdir_of`
returns `None`) and skip it forever instead of flagging it for manual review
— stranded debris, lower severity, same root cause.

`append_log()` itself also swallows any `OSError` on the log write silently
(lines 252–259: bare `except OSError: pass`), so even a *successful* run can
lose its audit record without any signal at all.

## Fresh evidence (built and run in this verify-pass, not reused from the
prior killed attempt's claim)

`/tmp/fworker-wq4505/probe_rmtree_fault.py`: builds two real git repos with
real orphaned worktrees (`wq100-a1`, `wq200-a1`), both `status='done'` in a
real sqlite wq db, makes `wq200-a1` raise a genuine `PermissionError` from
`rmtree` via `chmod 0o555` (no monkeypatching), and invokes the shipped CLI
exactly as production would:

```
reconcile_orphaned_worktrees.py --root <root> --apply --archive-root <dir> --log <path>
```

Result:

```
exit code: 1
PermissionError: [Errno 13] Permission denied: '.../worktrees/wq200-a1'
  (raised from inside shutil.rmtree, real traceback through run())
crashed from rmtree=True
wq100-a1 already deleted=True
wq100-a1 archive on disk=True
audit log never written=True
```

`wq100-a1` (processed first, alphabetically before `wq200-a1`) was already
archived, verified, and deleted from disk before the crash — and the `--log`
file was never created at all. This is a falsifiable probe: a patched version
that writes each JSONL record incrementally and wraps the archive+rmtree step
in `try/except OSError` would make this probe's asserts fail (log would
contain `wq100-a1`'s record; exit code 0) — that's the boundary it's pinned
to, per the ticket's acceptance-probe doctrine.

## Escalation

Per archetype-8 protocol, a contradiction of the author's (claude-sonnet-5)
implicit "done" verdict escalates to opus-5@high. Independently re-read
`run()`/`main()`, independently re-ran the probe, verdict: **CONFIRMED**,
medium severity (audit-trail loss, not data loss), fix required before the
tool's first `--apply` run against the real target
(`/home/lumen/worktrees/worktrees`, 2059 dirs, 46G — not yet run; no
production data lost by this gap so far). Sanity-checked against instinct 8
(fabricated evidence): the directory names, ticket-status semantics, and
2059-entry batch scale are all drawn from the shipped code and wq#4494's own
stated target, not invented values introduced into an unrelated system.

## Remediation (not applied — this is a verify-pass, not a re-implementation)

1. Write one JSONL record per directory as it is processed inside the loop,
   not batched at the end of `run()`.
2. Wrap the `archive_dir` success path's `shutil.rmtree(path)` call in
   `try/except OSError`, recording a `partial_delete` (or similar) action
   with the archive path and the error, and continuing the loop instead of
   crashing.
3. Stop swallowing `OSError` silently in `append_log()` — surface a log-write
   failure instead of losing it quietly.
4. Add the fault-injection probe above (or an equivalent monkeypatched unit
   test) to `tests/test_reconcile_orphaned_worktrees.py` as a permanent
   regression case.

## Scope note

Not evaluated: whether to block wq#4494's merged status retroactively — PR#2365
is already in `main` (commit `c738275e`) and the tool has not yet been run
with `--apply` against the real orphaned-worktree backlog, so no data or
audit record has actually been lost in production yet. This judgment's
recommendation is to fix before that first `--apply` run, not to revert the
merge.
