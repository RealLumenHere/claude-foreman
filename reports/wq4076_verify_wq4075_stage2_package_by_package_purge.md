# wq#4076 — verify-pass on wq#4075 (PR #2028)

**Verdict: CONFIRM**

Author: claude-sonnet-5 (wq#4075). Verifier: claude-sonnet-5@high, fresh instance,
archetype 8 adversarial verify.

## What wq#4075 claimed

`build.sh:79`'s single `chroot ... apt-get purge -y $pkgs` invocation meant one
package absent from the Xubuntu 26.04 base made apt-get exit non-zero for the
*whole* `bloat-removal.list`, purging nothing, while `|| log "... non-fatal"`
swallowed that exit code and misreported total failure as a benign partial.
Fix: purge package-by-package (`build.sh:76-93`) so one absent package only
skips itself; new `test_stage2_remove_bloat.py` with two assertions.

## What I checked

1. **Is the underlying premise real, or fabricated?** (instinct 8 check) — grepped
   the repo's own Xubuntu manifest research (`research/wq3983_xubuntu-26.04.1-desktop-amd64.manifest`,
   `research/wq3983_xubuntu-26.10-snapshot4-desktop-amd64.manifest`) for every
   package name in `bloat-removal.list`. All resolve **except `gnome-mahjongg`**,
   which has zero hits in either manifest in any form — while `gnome-mines` and
   `gnome-sudoku`, listed right next to it under the same "Games" comment block,
   are both present (`gnome-mines 1:49.0.1-2`, `gnome-sudoku 1:49.3-1`). That's
   consistent with `gnome-mahjongg` having been dropped from the Ubuntu 26.04
   archive entirely (not just "not installed" — a package merely not installed
   purges cleanly with exit 0 under real apt; only a package apt can't locate at
   all triggers the whole-invocation abort). So the bug's premise is grounded in
   this repo's own prior research, not invented for the ticket.

2. **Falsifiability of the new test** (wq#2630) — ran `test_stage2_remove_bloat.py`
   against both the pre-fix and post-fix `build.sh`:
   - Pre-fix (`git show 397b1b36:limpet/iso-builder/build.sh`): both tests FAIL.
     Captured stderr shows exactly the claimed bug reproduced end-to-end:
     `E: Unable to locate package thunderbird` → `stage2: some packages were
     already absent (non-fatal)` → `libreoffice-core was not purged even though
     it was never reported absent`.
   - Post-fix (current `build.sh`): both tests PASS.
   This is a real regression probe, not a tautology — it fails against the code
   it's meant to catch and passes against the fix.

3. **Full existing suite** — `python3 -m pytest` in `limpet/iso-builder/`: 22/22
   pass, including the pre-existing `test_build_theme_packages.py` (checks
   `bloat-removal.list` doesn't purge icon-theme packages) and others untouched
   by this diff. No regression.

4. **Code read** of the fix itself (`build.sh:76-93`): quoting is correct
   (`"$pkg"` vs. the old unquoted `$pkgs` word-split), the per-package `if/else`
   sits outside `set -e`'s reach (condition commands are exempt), the `<<<"$pkgs"`
   herestring runs the loop in the main shell (no subshell variable-scoping
   surprise), and an empty list still falls through to `autoremove` unconditionally,
   matching old behavior. `bloat-removal.list` itself has no CRLF, trailing
   whitespace, or inline comments that would break the one-package-per-line
   `read -r` parse.

## Things I looked for but didn't treat as blocking

- `test_stage2_remove_bloat.py` uses `thunderbird` as `ABSENT_PKG`, but
  `thunderbird` is actually *locatable* in the real archive as a transitional
  snap-wrapper deb (`research/...manifest:1749`) — it's a stand-in for "some
  package apt can't resolve," not a claim that thunderbird itself is the real
  trigger. The test's apt-get is faked, so this doesn't affect validity, just
  slightly undersells which list entry (`gnome-mahjongg`, per above) is the
  actual live trigger. Not worth a contradiction.
- `import os` in the new test file is unused. Lint nit, not a correctness issue.
- Per-package purging means a dependency conflict *between two listed packages*
  (not an absent-package case) would now also be swallowed under the same
  "not purged (absent from this base or already removed)" message, mislabeling
  a different failure class as benign. This is a byproduct of the ticket's own
  authorized design choice (option (a), "per-package tolerance") rather than a
  deviation from it, and I found no evidence any two entries in the current
  list actually conflict this way. Flagging for awareness, not as a defect.

## Conclusion

The fix matches the diagnosed bug, the diagnosis is grounded in this repo's own
manifest research (not fabricated), the regression test is genuinely falsifiable
(fails pre-fix, passes post-fix, both verified by direct execution), and the
full existing test suite still passes. CONFIRM — no escalation to opus-5@high.
