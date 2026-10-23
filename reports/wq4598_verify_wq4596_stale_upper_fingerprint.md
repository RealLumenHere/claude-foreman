# wq#4598 verify of wq#4596 — stale `.build/layers/upper` reuse let old sshd content through

- Commit: `a0fba532` (`fix(limpet-iso): wq4596 stage1b upper fingerprint + sshd pack
  guard`), merged to main via `1a6fb64e`. Gitea PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2444
  (MCP read-access to gitea unavailable this run — not allowlisted for this worker and
  the public API 404'd unauthenticated — so verification is against the merged commit
  content directly, which is the authoritative artifact).
- Author model: claude-sonnet-5. Verifier: claude-sonnet-5@high, fresh instance.
- Verdict: **CONFIRM**. The mechanism, the fix, and the probes are all correct. No
  escalation to opus-5@high.

## What the PR claims

A kept `.build/layers/upper/` from an earlier/different `stage1b_assemble_chroot` run
was reused on a later run with only an ownership check (`verify_layer_ownership`,
wq#4456), which says nothing about whether the kept `upper/` was unsquashed from the
squashfs *this* run is targeting. A stale upper from Oct 18 still held
`openssh-server` + host keys and would have been packed into the next build's target
layer undetected. Fix, two independent layers:

1. `layer_fingerprint()` in `build.sh` records `sha256(squashfs) + " " + layer name`
   to `$LAYERS_DIR/upper.fingerprint` when `upper/` is first unsquashed in
   `stage1b_assemble_chroot`. On reuse, it recomputes the wanted fingerprint and
   `fail`s (naming the exact `mv ... upper.stale-$(date ...)`) unless the stored
   fingerprint matches exactly — closing the "different base ISO / different
   `LIMPET_ISO_LAYER`" vector.
2. `verify_no_live_ssh()` in `stage11_build_hybrid_iso` refuses to pack any `upper/`
   containing `usr/sbin/sshd` or `etc/ssh/ssh_host_*_key`, independent of how it got
   there (stale reuse, or a later chroot stage installing it within the *same*
   squashfs+layer, which the fingerprint alone would not catch since the fingerprint
   is only pinned to the base squashfs, not upper's later mutations).
   `LIMPET_ALLOW_SSHD_IN_TARGET_LAYER` is an explicit, unset-by-default override.
3. Four new tests in `test_layered_remaster.py`: reuse-with-matching-fingerprint
   (positive control), refuse-missing-fingerprint, refuse-mismatched-fingerprint,
   refuse-sshd-in-upper (plus its override path).

## What I checked

- Read the full diff cold (`build.sh` +44/-0 across two functions + helper, 1 new env
  var in usage text; `test_layered_remaster.py` +67 new).
- Traced `stage1b_assemble_chroot`'s reuse branch end to end: `casper`/`layer`
  resolved before the fingerprint check runs, `want`/`got_fp` command substitutions
  both strip trailing newlines consistently, `sha256sum | cut -d' ' -f1` correctly
  takes field 1 of sha256sum's two-space-delimited output. Confirmed fingerprint is
  written only in the fresh-unsquash branch, after `unsquashfs`, so a crash between
  unsquash and fingerprint-write fails safe on the next run (empty `got_fp` ≠ `want`).
  `fail()` is `log ...; exit 1` — no silent-continue path.
- Checked the `verify_no_live_ssh` glob (`ssh_host_*_key`): matches all four standard
  OpenSSH host-key basenames (rsa/ecdsa/ed25519/dsa), does not false-positive on
  `*.pub` siblings (not sensitive), and the no-match case is safe under default (no
  `nullglob`) bash globbing — the loop body never executes against a literal
  unmatched glob string.
- Grepped the repo for `LIMPET_ALLOW_SSHD_IN_TARGET_LAYER` / `layer_fingerprint`: only
  referenced in `build.sh` and the new tests — no accidental default-on elsewhere
  (CI, wrapper scripts).
- Ran the new tests: `test_stage1b_reuses_upper_with_matching_fingerprint`,
  `test_stage1b_refuses_upper_with_missing_fingerprint`,
  `test_stage1b_refuses_upper_with_mismatched_fingerprint`,
  `test_stage11_refuses_sshd_in_upper` — all 4 pass.
  Full suite: `python3 -m pytest limpet/iso-builder/test_layered_remaster.py` → 27
  passed, no regressions.
- **Falsifiability check** (wq#2630 — a probe that can't fail confirms nothing): in a
  scratch edit (reverted after), neutralized each guard independently —
  (a) replaced the fingerprint-mismatch `fail` line with `true`,
  (b) wrapped the `verify_no_live_ssh` call in `if false; then ... fi` — and reran the
  targeted tests. Both mutations were caught: 3 of the 4 new tests failed against the
  neutralized code (`test_stage1b_refuses_upper_with_missing_fingerprint`,
  `test_stage1b_refuses_upper_with_mismatched_fingerprint`,
  `test_stage11_refuses_sshd_in_upper`), confirming these assertions are load-bearing,
  not tautological. Restored `build.sh` from a pre-edit backup; `git status` on
  `limpet/` is clean and the full suite (27/27) passes again post-restore.
- Considered but did not find load-bearing: upper's mutation-within-same-squashfs
  case (same base ISO/layer, sshd installed by a prior run's own later chroot stage
  before it was interrupted) — fingerprint alone would match on reuse here, but
  `verify_no_live_ssh` at stage11 (the terminal pack step) still catches it
  regardless of path taken, so the two guards are complementary, not redundant, and
  together close the full incident class described (Oct 18 upper found with
  openssh-server + host keys).
- Minor, non-blocking observation: `verify_no_live_ssh` checks for the sshd binary
  and host keys only, not broader live-ssh state (e.g. an enabled `ssh.service`
  symlink, `authorized_keys`). That's an exact match to what wq#4596 actually found
  and is in scope for a P2 fix; flagging as a possible follow-up, not a gap in this
  fix.

## Verdict

CONFIRM. No contradiction found.
