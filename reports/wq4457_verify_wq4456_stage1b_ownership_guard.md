# wq#4457 verify-pass: wq#4456 LIMPET stage1b EUID + stale-upper ownership guard

**Target:** PR #2335, merged `2348ba4473` on main (also present as `e19e48fa` on this
verify branch, same diff, squash-merge SHA differs as expected).
**Author model:** claude-sonnet-5. **Verifier:** claude-sonnet-5@high (this session, fresh
instance).
**Verdict: CONFIRM.**

## What the diff claims

wq#4456: a production rebuild via the external, untracked `build-limpet.sh` wrapper
(`/home/lumen/projects/limpet/iso-build/`) produced a 4.8G ISO vs 6.3G from an old
hand-built `upper/`. Claimed root cause, entirely in that external wrapper, not in this
repo: (1) a stale `LIMPET_ROOT` pointing at a non-checkout directory missing
`hardware-book/`/`hardware.d/`/`docs/`/`design/`, causing stage5/6/7/9 ENOENT; (2) the
wrapper's hand-listed stage loop skips `stage4_package_re_tools` and
`stage8_bundle_exploit_db` entirely. Claimed fix (in-repo, since the wrapper is outside
every worker's allowed directories, same boundary wq#3950 hit): `build.sh`'s own stage1b
previously ran without the EUID guard and stale-non-root-upper ownership check that
wq#4449 added only to the external wrapper, so running `build.sh` directly (README-
documented as supported) silently skipped both. Adds `verify_layer_ownership()` + a root
check inlined into `stage1b_assemble_chroot`, documents the wrapper's two gaps and the
fix-forward decision in README.md, and adds two tests
(`test_stage1b_refuses_non_root`, `test_stage1b_refuses_stale_non_root_upper`). Claims
69/69 iso-builder tests pass.

## What I checked

- Read the diff cold (`git show 2348ba4473` / `e19e48fa`): 53 lines added to `build.sh`
  (the `verify_layer_ownership` function + the `[ "$(id -u)" -eq 0 ]` guard + the
  upper-exists branch at `build.sh:142,161-166`), 61 lines of new tests, 41 lines of
  README. No unrelated changes.
- Independently confirmed the diagnosis's load-bearing facts rather than taking them on
  faith:
  - `stage4_package_re_tools` (`build.sh:272`) and `stage8_bundle_exploit_db`
    (`build.sh:457`) both exist in-repo and are both present in the canonical `STAGES`
    array (`build.sh:620-633`) in the documented order — the README's claim that the
    wrapper's hand-listed subset omits real, already-implemented stages is consistent
    with what's actually in this script.
  - `/home/lumen/projects/limpet` is confirmed outside this worker's allowed directories
    (`find`/`ls` on it were sandbox-blocked) — corroborates the "wrapper is out of
    worker reach" claim independently of trusting the author's own note.
  - `build.sh` already has `set -euo pipefail` (pre-existing, not part of this diff), so
    stage5/6/7/9's `cp`/`python3` calls already fail closed on ENOENT when run against a
    *correct* `LIMPET_ROOT` — i.e. there was nothing to "make fail-closed" in this
    script for the ticket's stage5/6/7/9 symptom; the bug really was wrapper-only
    (wrong path, not a missing guard), matching the documented decision not to touch
    those stages.
  - `kb/ISSUE-LOG.md` row 1497 and wq#4456's own notes mark status **partial**, honestly
    scoped ("in-repo fix + decision recorded; wrapper itself needs an operator with
    reach to /home/lumen/projects") — not overclaimed as fully done.
- Ran the two new tests directly: both pass.
- Ran the full suite: `python3 -I -m pytest limpet/iso-builder/ -q` → **69 passed**,
  matching the claim exactly.
- **Regression-proved the tests aren't vacuous** (wq#2630 doctrine: a probe must be able
  to fail): swapped the worktree's `build.sh` for the pre-fix version (`git show
  e19e48fa~1:...`) in place, reran just the two new tests — both genuinely **fail**
  against the pre-fix script (`test_stage1b_refuses_non_root` fails because the old
  script exits 0 with no root check at all). Restored the post-fix file afterward;
  `git status` confirmed clean, full suite re-run 69/69 passed.
- Read `verify_layer_ownership()` end to end for logic bugs: uid check is sound (non-root
  `unsquashfs` leaves every extracted file owned by the invoking user, so `! -uid 0`
  correctly flags it; a root-run `unsquashfs` of a stock squashfs — whose embedded
  metadata already stores `uid=0` for `/usr/bin/*` — correctly reproduces root
  ownership, so the function is only invoked on *existing* `upper/`, never on a freshly
  extracted one, which is the right scoping).

## Adversarial probing — what I tried to break

- **Mode-pattern robustness.** The setuid check uses a bash glob
  `4[0-7][0-7][0-7]` against `stat -c '%a'`. Isolated this exact pattern with
  `python3 -c "fnmatch.fnmatch(m, '4[0-7][0-7][0-7]')"` for `6755`/`2755`/`4755`/`4750`:
  confirmed `6755` and `2755` do **not** match, so any binary in the checked list that is
  setuid+setgid, or setgid-only, would be logged as "lacks setuid" and fail the guard —
  a false positive on an otherwise-legitimate, correctly-rooted `upper/`. This is a real,
  reproducible gap in the pattern. However: I could not find evidence it's reachable on
  the actual target (stock Xubuntu 26.04) — web search (`fleet-search web`) turned up a
  concrete `ls -l` listing showing `gpasswd` as plain `-rwsr-xr-x` (4755, setuid-only, no
  setgid) on a real Debian/Ubuntu system, and nothing suggesting any of
  `sudo/su/pkexec/passwd/newgrp/chsh/chfn/mount/umount/fusermount3` ship setgid-combined
  by default on current Ubuntu. Not escalating this as a contradiction — it's an
  unverified, likely-latent robustness nit against real-world inputs, not a demonstrated
  break of the ticket's actual acceptance population. Noting it here rather than filing a
  ticket, since I have no evidence it fires on any binary this pipeline actually ships
  against.
- Tried to find a path where `verify_layer_ownership` runs against a *freshly* extracted
  `upper/` (the two-run interaction this whole feature depends on): confirmed the branch
  structure (`if [ -d upper ]; then verify ...; else unsquashfs ...; fi`) only ever
  verifies a *pre-existing* `upper/`, and the EUID check earlier in the same function
  guarantees any fresh extraction happens as root — so there's no path where the guard
  checks a layer it just extracted itself under non-root privilege. No break found.
- Checked whether `test_stage1b_refuses_stale_non_root_upper`'s `fake_id=True` fixture
  could make the test pass trivially regardless of the guard (e.g. if `fake_id` also
  faked file ownership): read `FAKE_ID` (`id -u` → `0`) and the test body — it fakes only
  `id`, the stale `sudo` file is created for real at the test's own (non-root) uid, so
  the ownership check is exercised against real filesystem state. No shortcut found.

## Conclusion

wq#4456's diff does what it claims: `stage1b_assemble_chroot` now fails closed on
non-root invocation and on a stale non-root-owned `upper/`, exactly mirroring the guards
wq#4449 had only in the external wrapper. The two new tests are non-vacuous (proved by
reverting the fix and watching them fail) and satisfy the wq#2630 probe-must-be-able-to-
fail doctrine. The diagnosis of the wrapper (out-of-repo, out-of-reach, two independent
gaps) checks out against everything in this repo that can corroborate it. Full suite
69/69, matching the claim. **CONFIRM** — no escalation to opus-5@high warranted.
