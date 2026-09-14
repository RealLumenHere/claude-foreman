# wq#1925 — judgment on wq#1924's CONTRADICT of wq#1923 / PR #588

**Verdict: CONTRADICT UPHELD. Re-verdict on wq#1923's shipped diff: CORRECT-WITH-DEFECT, now fixed.**

wq#1923's own deliverable (reconciling `/home/lumen/foreman`) stands — wq#1924 re-ran
`repo_reconcile.py status` and got `IN_SYNC`, and PR #588's branch tip is byte-identical to
`origin/main`. Nothing about that is disturbed here. What was wrong is the code the diff
carried onto `main`, and wq#1924 called it correctly.

## The claim, checked live

wq#1924's repro used a copy of wq's regexes. This adjudication ran the real gate instead —
`/home/lumen/bin/wq` against a throwaway `WQ_DB`, no live ticket touched:

```
$ wq done 1 --notes 'fworker attempt 2: verified via artifacts after INDETERMINATE (session 0b1e2c34-…)'
-> 2 REFUSED: wq#759 evidence gate — task 1 not closed. `done` requires --notes naming a
   verifiable artifact …
$ wq done 1 --notes '… - landed commit 7ac064af43f4df489c248ce314cb82952775c139'
-> 0 done 1
```

That refusal satisfies `wq_done()`'s match condition (`"759" in out`) exactly, so the retry
shipped by PR #588 fires and closes the ticket with `--no-artifact "fire-mode/operational
closure - no code artifact"`. At that call site — `handle_resumable_failure()`, reached only
after `check_artifacts_done()` returned True — that reason is false: a real commit exists,
and `wq closure-quality` (wq:3010) now buckets a `verified` closure as `no-artifact`. The
claim holds on every point, and the failure lands in the one metric wq#759 exists to track.

Two details sharpen wq#1924's finding rather than soften it:

- The call site's `if wq_show(...) != "done"` guard means the *only* reachable route into
  that `wq_done()` is `check_artifacts_done()`'s commit branch — so a citable sha is not
  merely usually available, it is **always** available when the mislabel happens.
- The session UUID in the notes can never rescue the gate: `ARTIFACT_SHA_RE` (`wq:1877`)
  is a pure-hex `fullmatch` and a dashed UUID fails it, and `find_artifact(resolve_sha=True)`
  would reject it anyway.

## Disposition — both halves of the defect fixed

wq#1924 offered two fix shapes; they address different halves, so both are applied
(`harness/fworker.py`):

1. **The structural half** — `wq_done(ticket_id, notes, no_artifact_reason=None)`. The retry
   no longer infers artifact-lessness from refusal text; the caller asserts it. Only
   fire-mode's auto-close branch (`fworker.py:2003`) passes a reason, because it is the one
   caller that closes *because* no change was applied. Every other caller raises loudly on
   the refusal again, which is the behaviour that made this investigable before PR #588.
2. **The local half** — `check_artifacts_done()` now returns `(confirmed, commit_sha)`, and
   `handle_resumable_failure()` cites that sha in its notes, so the close passes wq#759's
   gate honestly and never reaches the retry. `(True, None)` means "already `done`, nothing
   to close", which also removes the second `wq_show()` and its race.

Fixing only (2) would leave the blanket retry waiting for the next caller; fixing only (1)
would turn today's silent mislabel into a `RuntimeError` on a ticket that genuinely landed.

**Tests** — `harness/tests/test_wq_done_artifact.py` (8): no retry without a caller reason;
retry uses the caller's reason verbatim; a still-refused retry raises; the wq#1673/wq#1702
deliverable refusal (exit 7) is never retried; `check_artifacts_done` returns the sha it
found and `(True, None)` when already done; `handle_resumable_failure` cites the sha and
passes no reason; already-done closes nothing; plus the end-to-end real-`wq` test above.
Full harness suite: 878 passed. The 12 remaining failures are pre-existing — they reproduce
standalone on files this ticket never touched (`test_token_injection.py`,
`test_wq1498_*`/`test_wq1539_*`, which assert properties of the live `wq` binary).
