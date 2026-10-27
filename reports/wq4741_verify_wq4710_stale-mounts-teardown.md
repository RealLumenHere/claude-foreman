# wq#4741 — verify wq#4710 [limpet-iso] stale overlay/proc/dev mounts under foreman iso-builder .build

Verdict: **CONFIRM**

## What wq#4710 shipped (PR #2533, merged ca0557d1a6 / c3e358b4)

`limpet/iso-builder/build.sh` gains a findmnt-driven `teardown_workdir_mounts` (known
chroot submounts + overlay + `layers/lower/*` squashfs in dependency-safe order, then a
depth-sorted catch-all for anything else under `$WORKDIR`), wired into `EXIT`/`INT`/`TERM`
traps that fire only on non-zero exit or a signal (a clean single-stage run keeps its
mounts for the next invocation, per the script's resumable-stage design). A new
`refuse_rm_if_mounted` backs a standalone `teardown` stage, and `stage1b_assemble_chroot`
now refuses to start (instead of silently remounting over) a `WORKDIR` with an orphaned
submount. `release_chroot` is generalized onto the same findmnt-driven path. Two files of
tests: 7 lines added to `test_layered_remaster.py` (a `FAKE_FINDMNT` fixture) and a new
`test_wq4710_teardown.py` (5 tests, including an actual `kill -TERM` against a running
`build.sh` asserting no mount survives).

## Checks performed

1. **Read cold**: diffed `af0df5fa..c3e358b4` (the PR's parent vs. the merge commit) for
   both touched files. Note: diffing `af0df5fa..ca0557d1` directly (the PR branch tip
   before merge) falsely appears to revert wq#4713's `readlink -f` HERE-resolution fix —
   that's an artifact of `ca0557d1` having been branched before `5f7765db` landed; the
   merge commit correctly carries both fixes. Confirmed current `build.sh:20` still reads
   `readlink -f "${BASH_SOURCE[0]}"`.
2. **Ordering**: `teardown_workdir_mounts` unmounts `$CHROOT`'s submounts and the overlay
   itself before `layers/lower/*` — the one case a generic deepest-path sort gets wrong
   (overlay and its lowerdir are siblings, not nested). Confirmed by reading, not just
   trusting the comment.
3. **Trap safety**: `WORKDIR`/`CHROOT`/`LAYERS_DIR` are assigned at top-level (lines
   30–35) before the traps are installed (line 153+), so an early failure before any
   stage runs still has valid values when the `EXIT` trap fires. Both signal and exit
   handlers clear all three traps before re-raising/exiting, so no double-fire.
4. **Wiring**: `teardown` is in `usage()`'s stage list and `main()`'s dispatch
   (`build.sh:1197-1198`), and `stage1b_assemble_chroot`'s new orphan-submount refusal
   sits after the existing "`$CHROOT` already mounted — reusing it" short-circuit, so it
   only fires on the specific gap the ticket named (mounted between proc/sys/dev/run and
   the final overlay mount, or a partial `release_chroot`).
5. **Adversarial**: checked whether `refuse_rm_if_mounted` is actually called by anything
   outside this script's own `teardown` stage (the commit message's "worktree prune,
   pruner timers" framing implies an external caller). Grepped the repo for any script
   that removes `LIMPET_ISO_WORKDIR`/`.build` — none exists; nothing external is wired to
   this guard yet. Not a contradiction: wq#4710's text asks only for the three mechanisms
   inside `build.sh` itself (trap teardown, a `teardown` stage, stage1b refusal) — wiring
   an external pruner through `refuse_rm_if_mounted` is future work this ticket never
   scoped.
6. **Immediate cleanup claim**: the ticket required clearing the live stale mounts under
   `/home/lumen/foreman/limpet/iso-builder/.build` as part of this ticket, not just a
   class fix. `ls -la` on `.build/chroot`, `.build/layers/lower`, and
   `.build/layers/lower/minimal` all come back empty (no `proc`/`sys`/`dev` entries, no
   squashfs content) — consistent with a torn-down overlay+bind-mount tree, not a
   directory that merely looks clean. `findmnt`/`mountpoint` themselves aren't on this
   worker's Bash allowlist, so this is the strongest check available without them; empty
   underlying directories are conclusive for bind/overlay mounts (a live mount would show
   the mounted filesystem's content through that path).
7. **Tests — ran myself, didn't trust done-notes**:
   - `python3 -m pytest test_wq4710_teardown.py -v` — 5/5 passed, including
     `test_sigterm_mid_run_tears_down_mounts` (sends a real `SIGTERM`, asserts rc=143 and
     the fake-mount bookkeeping file is empty afterward) and
     `test_clean_single_stage_exit_keeps_mounts` (falsifies the opposite direction — a
     clean exit must NOT tear down).
   - `python3 -m pytest test_layered_remaster.py` — 27/27 passed, no regressions from the
     `FAKE_FINDMNT` fixture addition or `release_chroot`'s generalization.

## Conclusion

The diff does what the ticket and PR describe: a findmnt-driven, dependency-ordered
teardown wired into EXIT/INT/TERM with the documented clean-exit exception, a `teardown`
stage, and a stage1b refusal on orphaned submounts, all covered by tests that can
actually fail (the SIGTERM test exercises the real trap path, and the clean-exit test
exercises the real no-op path). The immediate on-disk cleanup the ticket also required
checks out. No contradiction found; no escalation warranted.
