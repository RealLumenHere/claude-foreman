# wq#2016 — verify-pass: wq#1987 repo-sweep /home/lumen/bin CONTENT_DIVERGED

**Verdict: CONTRADICT** — the per-file attribution work (wq#1675→wq/slock,
wq#1659→unit-drift/wait-for-port, the ~20 scratch-file table, the two lpp-*
deletions) all hold up under adversarial re-check. But two of the shipped
commit's own factual claims about the state it left behind are wrong, and one
of them is a real, reproducible data-loss regression, not a documentation nit.

PRs: lumen-bin http://100.121.17.5:3000/lumen/lumen-bin/pulls/95 (merged
`c988a7e`, attribution commit `d96d5b1`); claude-foreman
http://100.121.17.5:3000/lumen/claude-foreman/pulls/635 (report doc, merged
`ae1c7fb`).

## What was verified and holds up

- `wq` / `slock` → wq#1675: ran the keeper script itself,
  `ops/wq1675-subsystem-ro/ensure_wq_subsystem_ro.py --check` (in
  `/home/lumen/foreman`), against the live `/home/lumen/bin/wq` and
  `/home/lumen/bin/slock` — all 12 anchors report `PATCHED`. The attribution
  isn't just plausible, the exact keeper that produced wq#1675's patch
  confirms the committed bytes match it.
- `unit-drift` / `wait-for-port` → wq#1659: `unit-drift`'s own
  `dangling_target_lint()` docstring self-cites `wq#1659`
  (`/home/lumen/bin/unit-drift:141`), matching wq#1659's ticket text
  ("dangling After/Wants/Requires target lint") verbatim. Not the
  wq#1675 the original ticket guessed.
- `lpp-cover-maybe-up` / `lpp-idle-guard` deletions: confirmed both live only
  under `/home/lumen/pokemon/tools/` now (`lpp-cover-maybe-up` present there,
  `lpp-idle-guard`'s job folded into `tools/lpp-cover-serve`), and
  `pokemon/docs/PORT-8871.md:75-83` independently documents
  `lumen-pokemon-live.service`'s `ExecStopPost` pointing at
  `tools/lpp-cover-maybe-up` and instructs deleting the two `bin/` copies.
  Deletion is correct, not data loss.
- `/trivy` precedent for gitignore-but-keep-on-disk: confirmed
  `/home/lumen/bin/trivy` is untracked, gitignored, and still present on
  disk — the pattern the commit says it's following for `gatus` actually
  works when done right (see contradiction below for where it wasn't).
- Ran the seven tests the commit claims are green post-merge
  (`test_slock_fence.py`, `test_wq_reopened.py`, `test_wq_needs.py`,
  `test_wq_show_json.py`, `test_wq_drift_guard_branch.py`,
  `test_wq_lease_fence.py`, `test_wq_merge_state_gate.py`): 58 passed, 0
  failed (commit message says "74 passed" — a different count, likely a
  wider `pytest tests/` invocation than just these seven files; not chasing
  this further since every file it names is green here too, and verify-pass
  isn't a re-implementation gate).

## Contradiction 1 — the gatus binary was NOT kept on disk as claimed (data loss)

`d96d5b1`'s message: *"Untrack it (keep the file on disk so nothing that
depends on it breaks) and gitignore it alongside the existing /trivy
precedent."*

`/home/lumen/bin/gatus` does not exist:

```
$ find /home/lumen/bin -maxdepth 1 -iname "gatus*"
/home/lumen/bin/gatus-ntfy-relay          # <- different file, not gatus itself
$ git -C /home/lumen/bin status --ignored --porcelain | grep -i gatus
(no output — not even present as an ignored file)
```

The diffstat for `d96d5b1` shows `gatus | Bin 50446590 -> 0 bytes` — the
shape of a plain `git rm gatus` (deletes the working-tree file too), not
`git rm --cached gatus` (the one that actually keeps the file on disk, which
is what was used for the `/trivy` precedent this commit cites as its own
model). The blob itself is still recoverable from history
(`git -C /home/lumen/bin cat-file -e 847dc04:gatus` succeeds, 50446590
bytes), so nothing is unrecoverable, but the live file the commit promised
to preserve is gone from `/home/lumen/bin` right now. ISSUE-LOG.md #66
(wq#1279, an earlier repo-sweep) lists this same `gatus` binary as one of
several "real fixes from other tickets... never committed" untracked files
worth keeping — i.e. this is not a throwaway scratch file by prior
determination, it's exactly the kind of file that repo-sweep is supposed to
protect.

## Contradiction 2 — "test cannot run until the module exists" is false

Both `d96d5b1`'s commit message and `reports/wq1987_repo_sweep_bin_content_diverged.md`
("Known follow-up") state that `tests/test_healer_stale_target.py` imports
`agent/lumen_self_healer.py`, which "does not exist anywhere in
`/home/lumen/bin`" / "this repo/tree", and that "the test cannot run until
that module exists."

Ran it exactly as its own docstring instructs
(`python3 tests/test_healer_stale_target.py`, not via pytest — it's a
standalone script, not a pytest suite, which is why pytest collects 0 tests
and looks like it "can't run" if you only check that way):

```
$ python3 /home/lumen/bin/tests/test_healer_stale_target.py
...
ALL CHECKS PASSED
```

All 8 checks pass. The module resolves via the test's own relative path
(`tests/../../agent/lumen_self_healer.py` = `/home/lumen/agent/lumen_self_healer.py`,
one level above the `lumen-bin` git repo, in a sibling tree this worker's
`--add-dir` grants don't cover, which is presumably why the original
attribution work never looked there). It exists, is 27065 bytes, and its
`mtime` is 2026-08-30 — three weeks before wq#1987's own attribution work,
and containing real stale-target/dedup/ticket-filing logic, not a stub. The
claim that wq#1112's implementation "was lost or never landed" is wrong; it
landed somewhere the sweep didn't check.

## Disposition

Attribution quality is good — every ticket mapping re-derives independently
from primary sources (self-declared comments, the wq#1675 keeper's own
`--check`, ISSUE-LOG cross-refs) and the two deletions are confirmed correct
against a completely different repo (`pokemon`). But the two claims above are
both about the resulting state, not about attribution, and both are
falsifiable checks that fail: a 50MB binary the commit says it preserved is
gone, and a test the commit says can't run, runs clean. Recommend (1)
restoring `/home/lumen/bin/gatus` from `847dc04:gatus` (still in history) and
re-verifying nothing broke while it was missing, (2) correcting the "known
follow-up" note now that the module is confirmed to exist (may just need a
wider `--add-dir`/path check next time, not a real wq#1112 regression).
Neither fix applied here — verify-pass is not a re-implementation gate
(precedent wq#872/wq#1268). Escalating to opus-5@high per this ticket's own
instructions.
