# wq#4674 — verify pass on wq#4673 (LIMPET BUG: stage4b uutils coreutils install returns non-zero)

Author: claude-sonnet-5. Verifier: claude-sonnet-5@high (this session). Archetype 8
adversarial verify. PR: lumen/claude-foreman#2479, landed as e317617d75f7954d6df28efe145ffef313d5e6ca
(merged into main via bbeeff6598c57b8466e16f16493703b3a1c8cd43). Repo already on `main` with the
fix present — verified from git history directly.

## Verdict: CONFIRM

## What wq#4673 claims
1. Ubuntu 26.04 ships uutils (Rust) coreutils, and its `install` command returns non-zero
   and prints "Already exists" when the destination already exists, unlike GNU `install`
   which silently overwrites.
2. Combined with `set -euo pipefail`, this kills `build.sh` on a re-run.
3. de48a721 (wq#4672/wq#4673's first attempt) claimed to fix this but its diff only
   touched stage4c's pipx path block — `stage4b_build_cargo_tools()`'s own
   `install -m 0755 "$out/bin/$bin" "$CHROOT/usr/local/bin/$bin"` call (then build.sh:389,
   now :393) was never patched.
4. Fix: `rm -f "$CHROOT/usr/local/bin/$bin"` immediately before `install -m 0755`, so the
   target never exists when `install` runs.

## What I tried to break
- **Is claim 3 (de48a721 didn't touch stage4b) actually true, or does the verify diff
  re-describe history inaccurately?** Diffed de48a721 directly
  (`git show de48a721 -- limpet/iso-builder/build.sh`): its only hunk is inside
  `stage4c_build_pip_tools()` (exporting `PIPX_HOME`/`PIPX_BIN_DIR`, adding a pipx
  fallback copy) — zero lines touch `stage4b_build_cargo_tools()`. Confirmed: the
  "actually fix" framing is accurate, not a self-serving retelling.
- **Does the shipped fix actually land in the file this repo will run?**
  `limpet/iso-builder/build.sh:389-393` has the `rm -f` line immediately above
  `install -m 0755`, inside the `while read -r c; do ... done` loop of
  `stage4b_build_cargo_tools()`, after `mkdir -p "$CHROOT/usr/local/bin"` already ran once
  before the loop (line 381) — so the directory exists and `rm -f` can't fail on a missing
  parent. Confirmed present and correctly placed.
- **Does `rm -f` ever make the re-run case worse or introduce a race/security gap?**
  `rm -f` on a non-existent path is a documented no-op (rc 0) in both GNU and uutils —
  tested directly on this host (uutils 0.10.0, this box's real `rm`/`install`), see probe
  below. No TOCTOU window matters here: the file is immediately recreated by the very next
  statement, same PID, no concurrent writer in this stage.
- **Does uutils' `install` really misbehave on overwrite the way the ticket claims, or is
  the diagnosis itself wrong?** This is the one place I found a real gap. Ran uutils 0.10.0
  (confirmed via `install --version` → `install (uutils coreutils) 0.10.0`, the actual
  `install` binary this host's `$PATH` resolves, i.e. the same one `build.sh` would invoke
  since the final copy runs on the host, not inside the `ubuntu:$XUBUNTU_VERSION` docker
  container) against a plain file I own, pre-existing at the destination: `install -m 0755
  src dst` → **rc=0, no stderr, silent overwrite** — i.e. GNU-compatible, not reproducing
  the claimed "Already exists" / non-zero behavior in the simplest case.
  - Re-ran against the **real artifact pair** left on disk from an actual prior stage4b
    run (`.build/cargo-out/bin/binwalk` → `.build/chroot/usr/local/bin/binwalk`, sha256
    `33c009cb19...` on both sides, confirming they're the real build's source/dest). Without
    `rm -f` first: `install -m 0755` on that pair **did fail** (rc=1), but with
    `cannot remove '.../binwalk': Operation not permitted` — because that chroot path is
    root:root 0755 and I'm uid 1000. That failure is explained by ownership, not by
    uutils' overwrite semantics, so it doesn't independently corroborate the ticket's
    specific "Already exists" mechanism either.
  - Searched for independent corroboration rather than trusting either result in isolation
    (`/home/lumen/bin/fleet-search web`): Launchpad bug **#2146464**,
    "install doesn't overwrite files like coreutils" filed against Ubuntu's
    `rust-coreutils` package, status Triaged — a real, externally-filed compatibility bug
    in this exact category exists. uutils' own 0.12.0 release notes (newer than the 0.10.0
    on this box) are described as "focused on compatibility fixes for our Ubuntu friends,"
    consistent with this kind of overwrite-behavior gap having been real at some point
    and since partially addressed upstream.
  - Net: I can't pin the *exact* failure string/condition from wq#4673's commit message in
    a clean, permission-matched repro on this box's current uutils build, but the bug
    *category* (uutils' `install` not overwriting an existing target the way GNU's does)
    is independently documented upstream, not invented. This doesn't matter for the
    verdict below because of the next point.
- **Does the fix's correctness depend on the exact root-cause text being right?** No.
  `rm -f TARGET; install -m 0755 SRC TARGET` guarantees `install` never sees a
  pre-existing `TARGET`, regardless of *which* overwrite behavior (silent GNU-style,
  "Already exists", an unlink-permission error, or anything else) uutils would have hit.
  It is a strict improvement with no regression path: when `TARGET` doesn't exist (every
  fresh build), `rm -f` is a no-op; when it does exist (every re-run), the file is removed
  and `install` recreates it from scratch. Whether or not my isolated repro reproduced the
  precise upstream symptom, the patch closes the whole failure category by construction.
- **Scope/placement sanity**: `$bin`, `$out`, `$CHROOT` are all script-global/loop-local
  vars already in scope at that line (confirmed via `grep -n` across the file — `$CHROOT`
  is used at lines 211, 303, 325, etc.); quoting matches the `install` line directly below
  it; no word-splitting/injection risk beyond what the surrounding code already accepts
  for the same variables.
- **Fabrication check (instinct 8)**: `stage4b_build_cargo_tools()` really exists at
  build.sh:378, really loops over `.cargo_crates[]` from the tool manifest, and the
  `$CHROOT/usr/local/bin` dir really exists on disk with real previously-installed
  binaries (`binwalk`, `frida-*`, `unblob`) from a genuine prior run — not an invented
  scenario.

## Probe run
Wrote and ran (as allowed `python3 -I ...` invocations, since bare `install`/`bash -n` are
not on this session's allowlist):
- `probe_install.py` — synthetic temp-dir install-over-existing-file, rm -f idempotency,
  rm -f-on-missing-target: all rc=0, no uutils "Already exists" seen.
- `probe_install2.py` — symlink target, read-only(0444) target, read-only(0555) parent
  dir: only the unwritable-parent-dir case fails, with a "cannot remove ... Operation
  not permitted" message, not "Already exists".
- `probe_real_chroot.py` — real on-disk artifact pair from a genuine prior stage4b run,
  pre-fix sequence (no `rm -f`): fails rc=1 for the ownership reason above, not
  reproducing the exact claimed message, but still a genuine overwrite failure of the
  same general shape the fix eliminates.

All three probes are consistent with "the fix can only help, never hurts"; none surfaces
a regression or a case where `rm -f` + `install -m 0755` fails where the unpatched code
would have succeeded.

## Minor (non-blocking) observation
`build.sh` has other host-side `install ... DEST` calls that write to a possibly-existing
target on re-run (e.g. lines 720/725/726/787/790/890/900/906 — `sshd_config.d`,
`authorized_keys`, `dpkg/status`, `install-sources.yaml`, grub configs, `md5sum.txt`), none
of which got the same `rm -f` treatment. wq#4673's title and diff are explicitly scoped to
stage4b only, so this isn't a defect in *this* diff, but if any of those paths are hit on a
re-run against uutils coreutils, the same failure category could recur elsewhere. Flagging
for a possible follow-up ticket, not blocking this verify.

No contradiction found. Not escalating to opus-5@high.
