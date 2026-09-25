# wq#2641 — verify of wq#2370 (PR#1002: fix(mask-replay): prefer the run's own frozen snapshot over a same-sized git guess)

Verifier: claude-sonnet-5@high, fresh instance. Author: claude-sonnet-5. Verdict:
**CONFIRM** — no contradiction, no escalation.

wq#2370 claims: `_recut()`'s fallback for a read-cache window whose live file has
since changed size picked the replaying revision by SIZE ALONE
(`blob_at_size()`), which can pick a same-sized-but-wrong git revision and never
even looked at the run's own gate snapshots (`_runs/wq<id>/snapshots/`) — bytes
this run itself already froze pre-image copies of before editing. The fix adds
`_stat_fp()` (recomputes guard.sh's `size:mtime:mtime_ns` fingerprint on a
candidate file) and `snapshot_for()` (finds the run's own snapshot whose fp
matches byte-exact), tries it in `_recut()` before falling back to
`blob_at_size()`, and tags every corpus key with a provenance suffix
(`#live`/`#snapshot:<name>`/`#git-history`/`#unresolved`).

I read the diff cold (`git diff` between the pre-fix branch tip
`feat/wq2370-…-a1` and the fix commit `95ca9e5` on `…-a3`, since `-a1` was an
earlier, unrelated attempt still sitting on the branch tip), then tried to break
it rather than trust the commit message.

## Per claim

| wq#2370 claim | Result | Evidence |
|---|---|---|
| `_stat_fp()` reproduces guard.sh's exact read-cache fingerprint format | CONFIRMED | `harness/guard.sh:562` writes `MTIME=$(stat -c '%s:%Y:%.9Y' "$FILE_PATH")`. `_stat_fp` computes `whole, frac = divmod(st.st_mtime_ns, 1_000_000_000); f"{st.st_size}:{whole}:{whole}.{frac:09d}"` — identical `size:sec:sec.nanofrac` shape. |
| `snapshot_for()` looks in the *actual* directory real runs write snapshots to | CONFIRMED | `mask_replay.snapshot_for` reads `RUNS_ROOT/wq<id>/snapshots`; `harness/fworker.py:3518` sets `env[action_gate.SNAPDIR_ENV] = os.path.join(run_dir(ticket_id), "snapshots")` and `run_dir()` (fworker.py:1427) resolves under the same `RUNS_ROOT`. Not a dangling assumption — traced the env var end to end. |
| `_sanitize_name` (duplicated, not imported, per its own docstring) matches `action_gate._sanitize`'s transform | CONFIRMED | Byte-identical regex/slice in both (`action_gate.py:497-498` vs `mask_replay.py:181-185`); a drift here would silently break every suffix match. |
| Newest-snapshot-first ordering (`sorted(..., reverse=True)`) is safe given the snapshot filename's timestamp prefix | CONFIRMED | `action_gate._stamp()` uses `%Y%m%dT%H%M%SZ` — fixed-width, lexicographically sortable, so reverse-sort is genuinely newest-first. |
| Snapshot preferred over git-history, git-history still the fallback | CONFIRMED | `_recut()` calls `snapshot_for` first when `ticket_id is not None`, only calls `blob_at_size` if that returns `(None, None)`. |
| Provenance suffix on corpus keys doesn't create duplicate/inflated report rows | CONFIRMED | `replay()`'s `seen` dedup set is keyed by `(label, value)` globally across `items.items()`, not by corpus key — so multiple provenance-tagged keys for what turns out to be identical bytes still collapse to one record. Traced this specifically because the extra key granularity looked at first glance like it could double-count. |
| New tests for this behavior pass | CONFIRMED | Built a throwaway worktree at the fix commit (`git worktree add /tmp/.../verify-a3 95ca9e5`, removed after); `pytest harness/tests/test_mask_replay.py -k "wq2370 or snapshot or recut or stat_fp"` → 5 passed. |
| No regression in the rest of the suite | CONFIRMED | Same worktree, full file: 62 passed, 1 deselected (see below). |

## Things I tried to break, that didn't

- Deleted-live-file path (`os.path.getsize` raises `OSError`): `_recut` returns
  `None, "read", "live"` immediately, **before** ever trying `snapshot_for` or
  `blob_at_size` — so a read of a file that no longer exists at all is dropped
  from the corpus with no fallback, not even `read-stale`. This looked like a
  gap in the fix at first. Checked the actual diff line-by-line against the
  pre-fix branch: this early-return is **unchanged** by wq#2370 — it exists
  identically before and after. The commit message scopes itself to "when the
  live file had changed size" (i.e., `os.path.getsize` succeeds), not the
  deleted-file case, so this is a pre-existing gap outside the PR's claimed
  scope, not a broken promise. Noting it here for the record, not as a
  contradiction.
- `test_wq1819_transcript_burned_nothing` (`assert len(records) == 1`) fails on
  this host: `assert 3 == 1`. Ran it on the **pre-fix** commit
  (`…-a1`, `f07901d`) in a separate throwaway worktree — identical failure,
  identical `3 == 1`. This is a host-state-dependent pinned regression test
  (`_runs/wq1819` on-disk contents have apparently drifted since the pin was
  written) that fails the same way with or without wq#2370's diff. Not
  attributable to this PR.

## Conclusion

Every load-bearing claim in wq#2370 reproduces: the fingerprint format matches
guard.sh's own read-cache write exactly, the snapshot directory wiring is
traced end-to-end through `fworker.py`'s env var to the same `RUNS_ROOT` this
module reads, the duplicated sanitize function hasn't drifted, ordering and
fallback precedence are as claimed, and the new/existing test suite passes
(62/63, the one failure reproducing identically without the fix). One
pre-existing, out-of-scope gap noted (deleted-file reads still skip
snapshot/git-history fallback entirely) but it isn't something wq#2370 claimed
to fix. **CONFIRM.**
