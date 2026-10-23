# wq#4546 — verify: wq#4527 openssh-server for live-session QEMU test SSH

**Verdict: CONTRADICT.**

PR #2391 (merged d0bc797a9b) adds `stage3b_enable_test_ssh` to
`limpet/iso-builder/build.sh`, gated to only install/authorize test SSH on a
`*.live` squashfs layer, skipping on every other layer. The commit message and
wq#4527's own closing notes both state the skip path cannot fail
`./build.sh all`: "every other layer logs and skips rather than failing
./build.sh all." That claim is false as written: the stage does a hard
`require_cmd systemctl` check *before* the layer gate, so a build host without
`systemctl` on `PATH` fails the whole `all` pipeline even when building the
default, non-live, production layer — the exact case the stage is supposed to
be a no-op for.

## What held up

- Layer-isolation claim (write only ever lands in the `*.live` layer's own
  overlay upper, never the production `minimal.standard` layer underneath):
  confirmed correct. `iso_layers.py:68` documents
  `minimal.standard.live` as "live-session only, never installed," and the
  layers stack as `minimal` → `minimal.standard` → `minimal.standard.live`
  (overlayfs lowerdir chain), so a live-layer build's upper/ writes cannot
  reach the lower `minimal.standard` layer stage3 fills for production.
- Key-based-auth-only / no hardcoded username claim: confirmed.
  `etc/skel/.ssh/authorized_keys` rides whatever user casper creates at boot;
  no file in the repo names that user.
- Fail-closed-on-missing-pubkey claim (live layer + no
  `LIMPET_TEST_SSH_PUBKEY` → `fail`, not a guessed default): confirmed,
  `build.sh:301`.
- `test_stage3b_enable_test_ssh.py`'s own 3 cases: all pass as shipped
  (`python3 -m pytest limpet/iso-builder/test_stage3b_enable_test_ssh.py -v`
  → 3 passed).

## What broke

`limpet/iso-builder/build.sh:287-298`:

```
stage3b_enable_test_ssh() {
  [ -d "$CHROOT" ] || fail "chroot not assembled yet ..."
  require_cmd systemctl                      # <- unconditional
  local layer
  layer="$(target_layer)"
  case "$layer" in
    *.live) ;;
    *)
      log "stage3b: $layer is not a live-only layer -- skipping test SSH ..."
      return 0
      ;;
  esac
  ...
```

`require_cmd` (`build.sh:49`) is `command -v "$1" >/dev/null || fail ...` —
a hard failure, not a log-and-continue. It runs before the `case` that decides
whether this stage does anything at all, so it fires on the skip path too.
`systemctl` is required nowhere else in this script (`grep -n systemctl
build.sh` → only lines 289 and 318, both inside this one stage) — this is a
new, stage-wide dependency the ticket's own design did not intend: the
skip branch needs nothing beyond `log()` (which needs `date`, already a
precondition every other stage in `STAGES` shares).

The author's own test suite cannot catch this: `_run_stage3b` in
`test_stage3b_enable_test_ssh.py` unconditionally writes a fake `systemctl`
into its `bin_dir` for *all three* cases, including
`test_default_layer_is_skipped_not_installed`. The "is systemctl present"
dimension never varies, so the skip-path test can't fail on this axis
regardless of statement order — exactly the "probe can't fail" shape
wq#2630 (cited in this ticket's own doctrine) warns about.

### Reproduction

Built a probe harness mirroring the shipped test's fixture style but with a
`PATH` containing only `date` + a no-op `chroot` shim (everything the
skip path legitimately needs) and no `systemctl` anywhere on `PATH`, targeting
the **default, non-live** layer:

```
$ /bin/bash build.sh stage3b_enable_test_ssh
# env: PATH=<bin_dir only>, LIMPET_ISO_WORKDIR=<tmp>, LIMPET_ISO_LAYER=minimal.standard
[06:38:41] FAIL: missing required command: systemctl (install it on the build host, not in the ISO's chroot)
# exit code 1
```

Same inputs with `require_cmd systemctl` hypothetically moved inside the
`*.live)` arm (not committed — verify-only, no re-implementation) would
return 0, matching the stage's documented contract.

## Why it matters

Every Lumen build host observed so far runs systemd, so this is latent today,
not actively failing production builds. But it directly contradicts the
diff's own stated invariant ("skips rather than failing `./build.sh all`" —
repeated in both the commit message and wq#4527's closing notes) and will
turn into a real `./build.sh all` outage the first time this pipeline runs
on any systemd-less build host (minimal container, chroot-based CI runner,
etc.) — exactly the kind of host this script's other stages (`stage1b`,
`stage4`) are written to run correctly from. One-line fix: move
`require_cmd systemctl` to after the `case` statement (inside the `*.live)`
branch, immediately before line 304-305's `apt-get install`/line 318's
`systemctl` call) so it only fires on the path that actually needs it.

## Disposition

Per archetype 8 doctrine, a CONTRADICT escalates to opus-5@high judgment.
Filed as wq#4560 (deps: wq#4546), decision options:

- (a) fix now — reorder `require_cmd systemctl` into the `*.live)` branch;
  one-line, zero behavior change on the live-layer path, removes the
  false invariant on every other layer.
- (b) accept as a known gap, log it, and defer — every current Lumen
  build host has systemd, so nothing breaks in practice yet.

## Judgment (wq#4560, 2026-10-23)

**(a) fix now.** The finding is upheld — re-confirmed live before touching
anything: `require_cmd systemctl` sat at `build.sh:289`, ahead of the layer
`case` at 292-298, and systemctl is called exactly once in the whole script
(`build.sh:317`, inside the live-only path). (b) was rejected because the gap
costs a one-line edit to close while a deferred known-gap row costs a doc
entry plus the next build host that lands without systemd — and the invariant
being wrong is what makes the stage's own doc comment false.

Fix: the `*.live)` branch is no longer an empty `;;` — it carries
`require_cmd systemctl`. Nothing else in wq#4527's diff was touched, per the
ticket's scope limit.

Test gap closed too. The shipped fixture stubbed a fake systemctl into PATH
for all three cases, so "is systemctl present" never varied (wq#2630
probe-can't-fail). `_run_stage3b` now takes `systemctl_on_path`, and when it
is false the fixture drops `/usr/bin` and `/bin` from PATH entirely —
otherwise the host's real systemctl answers `command -v` and the probe is
inert — leaving only the chroot/apt-get shims plus the three coreutils
build.sh needs before the layer check (`dirname`, `date`, `mkdir`). Two cases
use it:

- `test_default_layer_skips_without_systemctl_on_path` — the red case. Failed
  before the fix with `FAIL: missing required command: systemctl`, exit 1;
  passes after, logging the skip and exiting 0.
- `test_live_layer_without_systemctl_fails_closed` — the regression guard,
  proving the check moved rather than got deleted. apt-get is reachable on
  that stub PATH, so "openssh-server not installed" shows `require_cmd` fired
  ahead of the install rather than apt-get being absent.

Suite: 5 passed in `test_stage3b_enable_test_ssh.py`, 97 passed across
`limpet/iso-builder/`.
