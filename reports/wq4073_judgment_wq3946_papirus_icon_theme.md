# wq#4073 — judgment on the verify-pass CONTRADICT of wq#3946 (LIMPET BUILD: Xubuntu-based Limpet ISO)

**Verdict: CONTRADICT UPHELD, on the half that matters. The Papirus finding is
confirmed and unconditional: wq#3946 shipped a LightDM greeter conf naming three
external assets and installed none of them. The xubuntu-icon-theme half is
confirmed as a misclassification but its stated net effect ("purges the only
icon theme present") is **conditional and partly unverifiable here** — see
§3. Disposition: FORWARD-FIX, not reopen. Fixed in this branch; wq#3946 stays
closed. One out-of-scope latent defect found while verifying is filed, not
fixed.**

Author: wq#3946 (`ffd02823`, 2026-10-15 03:18). Verifier: wq#3955
(sonnet-5@high), report in worktree `wq3955-a3`, not landed. Judge: this pass
(opus-5), worktree `wq4073-a1`, against `447af117`.

## 1. What was checked, and how

Repo-wide `git grep -i papirus` over the worktree returns 4 hits and **no
install**:

- `docs/branding-spec.md:144`, `:161`, `:168` — the spec, naming the apt package
  `papirus-icon-theme` outright at `:161`.
- `limpet/iso-builder/overlay/etc/lightdm/lightdm-gtk-greeter.conf:4` —
  `icon-theme-name = Papirus-Dark`, shipped verbatim into the chroot by
  `build.sh` stage3.

(wq#3955 reported "exactly one reference"; it was counting build-affecting
files. The spec hits are real and they are what make the package name
authoritative rather than inferred — no correction needed to its conclusion.)

The only two apt-install paths in the whole script are stage4's RE-tool manifest
(`build.sh:131-132`, fed from `tool-catalog.json` via `gen_tool_packages.py`,
which emits only `.apt_packages` resolved from the tool catalog) and — now —
stage9. Before this fix, no stage installed any desktop theme or font.

## 2. The finding wq#3955 got right, and under-scoped

The greeter conf is nine lines and names **three** external assets, not one:

| greeter key | value | apt package (`branding-spec.md`) | installed by wq#3946? |
|---|---|---|---|
| `theme-name` | Greybird-dark | `greybird-gtk-theme` (:160) | no |
| `icon-theme-name` | Papirus-Dark | `papirus-icon-theme` (:161) | no |
| `font-name` | Inter 11 | `fonts-inter` (:126) | no |

wq#3955 caught the icon theme and missed the other two. The three are not
equally severe, and the difference is the interesting part:

- **Papirus-Dark is the real defect.** It is a third-party icon set, not part of
  any stock Xubuntu install. Nothing in the chroot provides it. This one fails
  on every build.
- **Greybird-dark is Xfce's own theme and the Xubuntu default**, so it is very
  likely already present via the base ISO. "Very likely" is the problem: the
  build was relying on an unstated base-ISO assumption that no line of the
  script or its README records.
- **Inter degrades by design.** `branding-spec.md:126` gives it an explicit
  fallback column (DejaVu Sans), so an absent `fonts-inter` is a spec'd
  graceful degradation, not a break. Installing it is a fidelity improvement,
  not a bug fix.

Runtime impact of the Papirus gap is cosmetic, not fatal: GTK falls back to the
default icon theme, so the greeter renders with the wrong indicator icons rather
than failing to start. wq#3955's phrasing ("LightDM boots with `icon-theme-name`
pointed at a theme absent from the chroot") is exactly right and does not
overclaim a brick.

The fourth asset the greeter names, `default-user-image =
/opt/limpet/brand/limpet-logo-128.png`, I checked and it is **fine** —
`design/limpet/brand/export-assets.sh:24` rasterizes it into
`$OUT/limpet-logo-128.png` and `build.sh` stage9 runs that with
`$OUT = $CHROOT/opt/limpet/brand`. Recorded because "the greeter conf is full of
dangling references" would have been the easy, wrong generalization.

## 3. Where wq#3955 overreached: the xubuntu-icon-theme claim

Two sub-claims, different standing.

**Confirmed: the classification is wrong.** `bloat-removal.list:20-21` listed
`xubuntu-icon-theme` under the comment "Games bundled by the xubuntu-desktop
metapackage". It is not a game. The package name alone settles that; no external
lookup is needed.

**Not established: "removes the one icon theme that was present."** Two reasons
this pass declines to adopt it as fact.

1. **The package-contents question could not be verified here.**
   `fleet-search web` is down (all four general engines suspended, no
   `BRAVE_API_KEY` — already logged, ISSUE-LOG rows 715/717, wq#2595), the
   `WebSearch`/`WebFetch` tools are not granted to this worker, and `apt-cache`
   is not on the Bash allowlist. wq#3955's Launchpad citation ("Xubuntu icon
   theme (transitional package)") is plausible and I have no reason to doubt it,
   but if it is right then purging it is *also* not simply "removing the icon
   theme" — a transitional package's real payload lives in the package it
   depends on, and `apt-get purge` of a transitional package cascades to its
   reverse-dependencies (plausibly `xubuntu-artwork`, possibly `xubuntu-desktop`)
   rather than to its dependencies. That is a *different and larger* failure mode
   than the one wq#3955 described, and I will not assert either without package
   metadata.
2. **Stage 2 may not purge anything at all.** `build.sh:79` passes the entire
   list to a single `apt-get purge -y $pkgs`. If *any* one package in the list is
   absent from the base ISO, apt exits non-zero having purged **nothing**, and
   the `|| log "stage2: some packages were already absent (non-fatal)"` on the
   same line swallows it. So wq#3955's net-effect claim is conditional on every
   other package in the list resolving. See §6.

None of this weakens the disposition: the Papirus half stands on its own, and
an icon-theme package does not belong in a games block under either reading.

## 4. Disposition: forward-fix, not reopen

Reopen loses on the mechanic this repo has already documented. `wq claim` prints
only a ticket's `detail` field (ISSUE-LOG row 717, citing `wq:1752`/`:1809`), so
reopening wq#3946 re-dispatches its original ten-task spec with no record of
what the verify pass found — the wq#2591 mechanic, and the same reasoning
wq#2601 used to decline a reopen. wq#3946 is a landed 12-stage build script;
re-running that spec to change six lines is the expensive way to get a worse
result. The defect is self-contained, the correct values are named by a spec
already in the tree, and the fix is small enough to carry its own regression
test. Fixed here.

## 5. The fix

- `limpet/iso-builder/build.sh:200-208` — stage9 installs
  `papirus-icon-theme greybird-gtk-theme fonts-inter` into the chroot. Placed in
  stage9 (branding) rather than stage2/3 because it runs after stage2's
  `apt-get autoremove`, and because stage9's own comment ("LightDM greeter:
  shipped verbatim in overlay/, nothing more to do") was the false claim being
  corrected. Greybird and Inter are included even though they are lower severity:
  leaving them makes the greeter conf's self-consistency depend on an
  unrecorded base-ISO assumption and invites a third verify pass on the same
  nine lines. Installing an already-present package is a no-op for apt.
- `limpet/iso-builder/bloat-removal.list:20-26` — `xubuntu-icon-theme` dropped
  from the games block, with the reason left in place of the line so the next
  reader does not re-add it.
- `limpet/iso-builder/build.sh:98-101` — stage3's `cp -a "$LIMPET_ROOT"/*.py`
  replaced with a `find ... ! -name 'test_*.py' -exec cp -a` so the pytest suite
  stops shipping into the production `/opt/limpet/lib`. wq#3955 reported 7 files
  (it matched only the `test_limpet_*` prefix); the real count is **17** of the
  34 `.py` files in `limpet/` — half the staged tree was tests.
- `limpet/iso-builder/README.md:47-55` — recorded in the script's own
  "Interpretation decisions" section, where every other such call is recorded.

### Falsifiability of the probe (wq#2630)

`limpet/iso-builder/test_build_theme_packages.py` — three tests, all of which
**fail against the pre-fix tree** (run before editing: 3 failed in 0.06s) and
pass after (`python3 -m pytest limpet/iso-builder/ -q` → 20 passed). They are
not drawn from the population the fix was designed around:

- `test_greeter_assets_are_installed` parses the asset names **out of the
  greeter conf itself** rather than hardcoding them, so adding a fourth
  `*-theme-name` key without a matching install fails the test — an input class
  the fix was not written against.
- `test_bloat_removal_purges_no_icon_theme` probes the file the fix edits in the
  *opposite* direction (a deletion), so reverting either edit is caught
  independently of the other.
- The `find` filter was additionally probed directly: 17 paths selected, 0
  matching `/test_`.

## 6. Out of scope, filed not fixed

`build.sh:79`'s single-invocation `apt-get purge -y $pkgs` with a `|| log`
swallow means one absent package silently cancels the entire bloat removal, and
the log line actively misreports it as a benign partial. Fixing it means
deciding whether a failed purge should be fatal, or whether the list should be
purged package-by-package with per-package tolerance — a behavioural change to
the build's failure semantics with its own acceptance criteria, not something to
smuggle into an icon-theme fix. Filed as **wq#4075** (build, pri 2, deps
wq#3946).

## 7. Scoring wq#3955

Correct to flag it, correct that it sits inside wq#3946's own task 9, correct
that it is a self-contained inconsistency between two files the same diff ships
and therefore not an inherited gap. It under-counted the shipped test files
(7 vs 17), missed the two sibling assets in the same nine-line file, and stated
a net effect for the purge that it could not fully support. The core call —
CONTRADICT — holds.
