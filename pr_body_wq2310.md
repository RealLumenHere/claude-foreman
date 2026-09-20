# wq#2310 — [rotation] burned credential in wq#2291 transcript (kv-secret)

## What

**Verdict: false positive. No credential was burned, nothing was rotated.**

The tripwire filed this ticket at 22:30:11Z against wq#2291 attempt 1 — and wq#2291
was itself the rotation ticket for wq#2278, so this is the third generation of one
chain.

The rule live at that second is frozen in the run's own gate snapshot,
`_runs/wq2291/snapshots/20260920T222917Z-…masking.py:32-34` (2137 bytes, matching
the `fp` size the reader recorded in `events/read_cache/3568d8ae-….jsonl`): the
naked-keyword kv rule, no trailing lookahead. Every fix that would have silenced it
landed later — `32aa000` at 22:40, `345bf49` at 22:57, `281327c` at 23:09 — so no
conclusion drawn from today's patterns applies to the alarm itself.

Replaying that rule over every window wq#2291 actually read, and then over the whole
reachable corpus (1368 files: `_runs/wq2291`, the event log, `kb/ISSUE-LOG.md`, and
the foreman tree at `27c1fd5`, the commit that was HEAD at 22:30:11):

- 67 distinct values matched, **0 of them live** (`burn_filter.live_secret_values`)
- every one is a committed source literal; the apparent exception, a 40-char oauth
  dummy at `harness/tests/test_token_injection.py:39`, reads as uncommitted only
  because `is_committed_literal` greps HEAD and `74dcb82` has since deleted it

The trigger is reproduced, not inferred: `harness/masking.py:55` — the wq#2288
justification comment quoting the called-identifier example — yields exactly the
recovered `len=19 sha256[:12]=eb522505a0e3` under the deployed rule, and 0 under the
rule on main. wq#2291 was writing that comment into its worktree between 22:29:49
and 22:30:10. **The alarm fired on the worker writing the fix for the alarm**, six
minutes before the same fix landed as `32aa000`.

## Why

The leak path is already closed, and this verifies that rather than re-fixing it.
`e0a4d06`, `32aa000`, `99ae7fb`, `345bf49`, `281327c` and `74dcb82` are all deployed
(`/home/lumen/foreman` clean at `74dcb82`). Replaying all 18 frames of wq#2291's read
corpus through the live path — `mask_text` → `burn_filter.matched_values` →
`classify` — pages **0 times**.

What was never pinned is the file that fired. `harness/fworker.py` holds
`mask_alarm`, `screen_mask_hits` and `write_mask_hits`, wq#2291 read it five times in
one attempt, and it carries three old-rule hits — yet the suite pinned only
`masking.py` (wq#2296) and the test file itself (wq#2303), one file per ticket, each
added after that file had already paged someone. The invariant is not per-file:
everything the alarm is made of gets read by the worker the alarm sends.

## How

Folds those two hand-rolled single-file pins into one parametrized
`test_the_alarms_own_read_path_does_not_re_arm_it` over all eight files the alarm is
built from — the rules and their justification comments (`masking.py`,
`burn_filter.py`), the alarm itself (`fworker.py`), the three test files a rotation
worker necessarily reads, and the token-engine mirror pair.

Test-only change; no behaviour moves. Two re-arms are left deliberately and named in
ISSUE-LOG row 593: `read_dir_sweep.py:57` still matches on its own pointer-example
comment (benign under `classify`; the carve-out is wq#2297's port), and
`/home/lumen/bin/mask-secrets` is still unanchored (wq#2313).

## Testing

- `harness/tests/test_masking.py` `test_burn_filter.py` `test_token_injection.py` — 50 passed
- `harness/tests -x` — 664 passed before stopping on one pre-existing failure
  unrelated to this change (`test_fworker_grant.py::test_grant_file_is_0400_and_expires_from_wall_clock`,
  mode `0o444` vs `0o400`), reproduced on clean `main` in `/home/lumen/foreman`; the
  rest of the suite runs with that test deselected
- the new pin has teeth: `masking.scan()` on `read_dir_sweep.py` returns a non-empty
  record list, which is the exact assertion this test makes
- the new ISSUE-LOG row was scanned through `masking.scan()` before being written —
  0 records, so documenting this one does not arm the next

## Risk / rollback

Low. One test file changes; no production module is touched, so the worst case is a
failing pin rather than a changed alarm. Rollback is `git revert` of this commit,
which restores the two single-file pins it subsumes.
