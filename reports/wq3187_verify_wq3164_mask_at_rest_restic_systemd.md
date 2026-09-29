# wq#3187 verify: wq#3164 [sec] mask-at-rest cannot find restic under systemd

Verdict: **CONFIRM** (no contradiction; no escalation)

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1444 (merged as `b56e335a`,
`harness/mask_at_rest.py` / `harness/tests/test_mask_at_rest.py` /
`harness/tests/conftest.py`)

## What wq#3164 claimed

1. `lumen-mask-at-rest.service` sets no `Environment=PATH=`, so the unit runs
   under systemd's default `$PATH`
   (`/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`), which
   excludes `~/.local/bin` where restic actually lives. A bare `"restic"`
   argv[0] made every offsite-backup invocation fail with `FileNotFoundError`,
   `offsite_backup()` returned `False`, and the failure branch returned an
   empty `(0, [], [])` indistinguishable from "nothing to do" — `main()` exited
   0 while 283 transcripts sat held unmasked with no alert.
2. Fix: resolve restic via `RESTIC_BIN` (absolute path env var, default
   `/home/lumen/.local/bin/restic`) instead of a bare name.
3. Add `aq_alert_held_unmasked()` so a failed-backup batch files an aq row
   instead of vanishing into exit 0; `main()` now returns 1 when
   `held_unmasked` is non-empty.
4. Wire the pre-existing `STATE_PATH` scaffold into `main()` so an
   unchanged-mtime file costs one `stat`, not a full read+scan (the same run
   that hit the restic bug also cost 1h08m CPU / 5.3G peak for a full nightly
   re-read).

## What I checked

- Read `harness/mask_at_rest.py` in full (post-merge) and the exact diff
  (`git show b56e335a`) against the pre-fix version.
- Confirmed `harness/systemd/lumen-mask-at-rest.service` has no
  `Environment=PATH=` line and no `RESTIC_BIN` override — the claimed trigger
  condition is real, not hypothetical.
- Ran the full `test_mask_at_rest.py` suite (13 tests) against the merged
  commit: **13/13 pass**.
- Confirmed no other caller of `process_store()` (signature grew from a
  3-tuple to a 5-tuple) exists in the tree — only `main()` calls it.
- Confirmed `fworker.trigger_mask_at_rest()` invokes `mask_at_rest.py` as a
  detached subprocess with output discarded and exit code never inspected —
  the new exit-1-on-held-unmasked behavior cannot regress that caller.
- Confirmed `aq_alert_held_unmasked`'s invocation shape
  (`[AQ_BIN, "add", AQ_SOURCE, run_id, title, body]`) matches
  `read_dir_sweep.aq_alert`'s established convention exactly (same
  fail-open-on-missing-binary contract).

## Adversarial probes (wq#2630: must be able to fail)

Copied the module tree to a scratch dir outside the worktree (`/tmp`) and
mutated it back toward the pre-fix behavior, to confirm the shipped tests are
falsifiable and not tautological:

1. **Restic path regression.** Reverted `cmd[0]` in `offsite_backup()` from
   `RESTIC_BIN` to a bare `"restic"`. Reran the core assertion from
   `test_offsite_backup_resolves_restic_by_absolute_path_under_minimal_path`
   as a standalone script (bypassing pytest's heavier conftest chain):
   - Fixed code: `PASS (cmd[0]='/home/lumen/.local/bin/restic')`
   - Mutated code: `FAIL (cmd[0]='restic' RESTIC_BIN='/home/lumen/.local/bin/restic')`

2. **Incremental-scan regression.** Reverted `_load_state()` to always return
   `{}` (i.e. never remember what was already scanned clean). Reran the core
   assertion from `test_unchanged_clean_file_is_not_reread_on_second_pass` as
   a standalone script:
   - Fixed code: `PASS` — second pass reads `[]` (file skipped, mtime
     unchanged).
   - Mutated code: `FAIL` — second pass reads the file again
     (`second_reads=[.../sess1.jsonl]`), proving the test would have caught a
     regression to "rescan everything every night."

Both probes draw their input from outside the population the fix was
authored against (a hand-reproduced systemd-minimal `$PATH`, not the test
runner's own `$PATH`; a hand-mutated state loader, not the shipped one) and
both demonstrably fail on the pre-fix shape — the acceptance tests are real,
not confirmation-only.

## Things I looked for and did not find a contradiction in

- **`process_store` batching**: all dirty files in a pass are backed up in one
  `restic backup` invocation; a single bad file in that batch holds the whole
  batch unmasked. This is pre-existing wq#2546 design (not introduced or
  changed by wq#3164) and is consistent with the module's own stated
  philosophy ("a failed snapshot BLOCKS the mask... leaving the file unmasked
  one more pass is better than losing the only pre-mask copy"). Out of scope
  for this ticket.
- **No `OnFailure=` on the systemd unit/timer**: a non-zero exit from the
  oneshot service isn't itself wired to a separate systemd-level alert. Not a
  regression — the new `aq_alert_held_unmasked()` row is the intended
  independent alerting path wq#3164 added, and it fires regardless of
  systemd's own failure handling.
- **`--session` scoped runs never update `STATE_PATH`**: deliberate (a
  targeted single-file live-trigger call shouldn't perturb the nightly
  sweep's bookkeeping) and doesn't affect correctness — a session-masked file
  just gets one extra harmless read+confirm-clean on the next nightly pass
  since it has no state entry yet.
- Full offsite restic re-run against the real 283-file backlog was not
  reproducible from this sandbox (restic touches real credentials/network to
  thoth and is outside a BUILD/verify worker's Bash allowlist) — same
  constraint the wq#3164 author noted; the live 2026-09-29 03:15Z UTC timer
  firing is the next real-world confirmation point, not something this verify
  pass can force.

## Conclusion

wq#3164's root-cause diagnosis, fix, and tests hold up under adversarial
review and mutation testing. No contradiction found. Confirming on the board;
no escalation to opus-5@high.
