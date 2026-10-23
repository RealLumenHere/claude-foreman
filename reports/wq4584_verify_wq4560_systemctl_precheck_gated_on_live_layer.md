# wq#4584 — verify: wq#4560 judgment on wq#4546's CONTRADICT of wq#4527

**Verdict: CONFIRM.**

PR #2422 (commit `70496545`, merged into `main`/`origin/main`) implements wq#4560's
judgment option (a): move `require_cmd systemctl` from ahead of the `case "$layer"`
statement in `stage3b_enable_test_ssh` (`limpet/iso-builder/build.sh`) into the
`*.live)` branch, so the default/non-live skip path no longer hard-fails on a
systemd-less build host.

## What I checked

- **Diff scope** (`git show --stat 70496545`): exactly the three files the ticket
  scoped — `build.sh` (+7/-4), `test_stage3b_enable_test_ssh.py` (+81/-11), and the
  wq#4546 report (judgment addendum). No wholesale re-implementation of wq#4527.
- **The fix is the one-line move the ticket called for**: `build.sh:296` now reads
  `*.live) require_cmd systemctl ;;` in place of the empty `*.live) ;;`; the `*)`
  branch (every other layer) still `return 0`s before reaching line 321, the only
  other site that calls `systemctl` in the whole script (confirmed via
  `grep -n "systemctl\|require_cmd" build.sh` — systemctl used exactly once, at
  line 321, strictly inside the now-guarded branch).
- **Adversarial re-run — proved the test is not vacuous.** Extracted the pre-fix
  `build.sh` (`git show 70496545^:limpet/iso-builder/build.sh`) into an isolated
  temp dir alongside the *post-fix* test file and ran pytest there:
  `test_default_layer_skips_without_systemctl_on_path` **fails** against the old
  code (`FAIL: missing required command: systemctl`, exit 1) and **passes** against
  the new code — a real red/green, not a probe that can't fail (the wq#2630 shape
  this whole chain exists to close). `test_live_layer_without_systemctl_fails_closed`
  confirms the guard moved rather than vanished: the live layer still hard-fails
  without systemctl on PATH.
- **Full suite**: `python3 -m pytest limpet/iso-builder/` → 97 passed, matching the
  judgment note's own count. No regressions elsewhere in the directory.
- **Landed state**: `70496545` is an ancestor of `origin/main` HEAD — this is not a
  pending PR, it's already shipped.
- **ISSUE-LOG.md row 1551** already documents this as SOLVED with matching root
  cause/solution text; no new row needed.

## What I tried to break

- Checked for other `*.live`-shaped layer strings that might dodge the glob
  (`case "$layer" in *.live) ... ; *) ...`) — the pattern is unchanged from
  wq#4527's original gate, out of this diff's scope, and already covered by
  `test_live_layer_installs_ssh_and_authorizes_key` / `test_default_layer_is_skipped_not_installed`.
- Checked whether the live-path behavior changed at all (it shouldn't — the
  precheck still runs unconditionally for any layer that reaches the branch):
  confirmed identical via the two live-path tests, both passing.
- Checked whether `systemctl` has a second call site outside the guarded branch
  that would now go unchecked: none (only call site is `build.sh:321`, inside the
  branch).

Nothing contradicts the shipped fix. Confirming on the board.
