# wq#3955 — Adversarial verify-pass of wq#3946 (LIMPET ISO build pipeline, PR#1907)

**Verdict: CONTRADICT.** wq#3946's own cited core deliverable (`build.sh`'s task 9,
"Branding assets... LightDM greeter theme") ships a greeter config that references two
packages this diff never installs, and `bloat-removal.list` (also shipped by this diff)
purges the one icon theme the stock base ISO *does* carry — miscategorized as a game. Net
effect on a real build: LightDM's `icon-theme-name` points at a theme absent from the
chroot, with no fallback left either. This is a defect inside the diff's own scope, not an
inherited or already-flagged gap. A secondary, lower-severity finding (test files shipped
into the production chroot) is also recorded below.

Everything else checked — the automated tests, the two Python helpers, the driver-bundle
hook, every "spec file doesn't exist" / "no package-name field" claim, every ISSUE-LOG
citation — held up exactly as claimed. This is a narrow, surgical contradiction, not a
wholesale rejection of the PR.

## What was shipped (PR#1907, commit `ffd0282`, 16 files, +813)

`limpet/iso-builder/{build.sh,gen_tool_packages.py,gen_motd.py,bloat-removal.list,
README.md,overlay/,test_gen_motd.py,test_gen_tool_packages.py}`. `build.sh` is 12
independently-runnable, fail-fast stages (`stage1`..`stage12`, `5`/`10` merged — the
ticket names both as "hardware profile book TOML profiles", a real duplication in wq#3946's
own task list, correctly spotted and merged).

## Probes that passed

- **9/9 pytest, pinned.** Reconstructed `ffd0282`'s exact `gen_tool_packages.py` +
  `test_gen_tool_packages.py` + `gen_motd.py` + `test_gen_motd.py` + `tool-catalog.json` +
  `tokens.json` into an isolated scratch tree (not the now-patched `main` copies — `main`
  gained a `license_policy` gate via wq#4048 after this merge) and ran them standalone:
  `9 passed in 0.03s`, matching the done-notes claim exactly, against the author's own
  pinned inputs, not today's superset.
- **stage4 exercised for real**, not just read: ran `gen_tool_packages.py` directly against
  the pinned `tool-catalog.json` (34 real tools) on this build host's real `dpkg` database —
  5/34 resolve to real apt packages already installed here (`binwalk`, `flashrom`,
  `openocd`, `qemu-user`, `sigrok-cli`), 29 correctly unresolved (never guessed).
- **stage7's hook exercised for real**: ran `limpet/hardware.d/build_iso_drivers.py
  --write-bundle` directly — produces `90-limpet-hardware.rules`, `prebake-plan.md`,
  `PROPRIETARY-LIMITATIONS.md`, the exact three filenames `build.sh:167-168` expects to
  `cp`.
- **stage9's MOTD renderer exercised for real**: `gen_motd.py` against the pinned
  `tokens.json` produces correctly-nested ANSI escapes (`\033[33m`...`\033[96m`...`\033[0m`
  per line), matching `test_gen_motd.py`'s reset/role assertions.
- **Every "does not exist" claim reverse-checked, not trusted**: `install-wizard-spec.md`
  — zero hits repo-wide (only referenced *documenting its absence* in this PR's own
  README). "Precheck service" (Limpet/appliance sense) — zero hits outside this PR's files
  (the 90 "precheck" hits elsewhere are `harness/node_lifecycle.py`'s unrelated fleet-node
  precheck). `docs/target-wizard-spec.md`, `docs/desktop-spec.md`, `docs/branding-spec.md`,
  `limpet/tool-catalog.json`, `limpet/hardware-book/`, `limpet/hardware.d/*.toml`,
  `ops/wq3809-limpet-hal/hal.py`, `ops/wq3808-limpet-mcp-meta-server-a1/*`,
  `research/wq3921_soc_exploit_seed.json` — all exist exactly where `build.sh` reads them.
  `kb/ISSUE-LOG.md` rows 1327 and 1329 cited in the README/header comments match verbatim.
- **Could not run `build.sh` itself** (any stage, `--help` included): every shape —
  `bash build.sh`, `./build.sh`, `ssh lumen@...`, `cd ... && ./build.sh` — is refused by
  this worker's allowlist (`harness/worker-settings.json` has no bare-script-execution
  entry), the identical wall two prior attempts on this same ticket hit and were KILLED for
  retrying (`_runs/wq3955/failure.json`, 6x `CAPABILITY_DENIED`). Worked around it by
  invoking the stage's actual underlying command directly via `python3` (allowed) instead
  of through the shell wrapper — same effect, no retry of the blocked shape. The remaining
  gap (`stage1`'s checksum/extract logic, `stage11`/`stage12`'s real ISO assembly and QEMU
  boot) needs a real build host with `debootstrap`/`xorriso`/`qemu-system-x86_64`/15GB
  scratch, which neither this sandbox nor the author's had — the README says so explicitly
  and that claim is honest, not an evasion.

## The contradiction: the branded LightDM greeter references packages this diff never installs

`docs/branding-spec.md` is explicit and unambiguous about the appliance's theme pick:

- `docs/branding-spec.md:144` (§4.1 LightDM greeter, the literal `[greeter]` ini block):
  `icon-theme-name = Papirus-Dark`
- `docs/branding-spec.md:160-161` (§4.2 XFCE desktop theme): "GTK/WM theme:
  **Greybird-dark** (`greybird-gtk-theme`...). Icon theme: **Papirus-Dark**
  (`papirus-icon-theme`) — has the symbolic/colored coverage the §4 genmon `<img>` names
  resolve against" — i.e. the spec names `papirus-icon-theme` as the actual apt package,
  and ties it directly to the `<img>` icon names this very PR's own genmon scripts emit
  (`overlay/opt/limpet/panel/genmon-hardware.sh`: `<img>network-wired</img>` /
  `<img>network-offline</img>`).

wq#3946's shipped `overlay/etc/lightdm/lightdm-gtk-greeter.conf` carries this forward
verbatim:

```
theme-name = Greybird-dark
icon-theme-name = Papirus-Dark
```

(`greybird-gtk-theme` ships by default on stock Xubuntu — no action needed there, and
none is a defect. `papirus-icon-theme` is a universe-repo package that is **not** part of
the stock Xubuntu desktop metapackage and needs an explicit `apt-get install`.)

No stage of `build.sh`, no entry of `bloat-removal.list`, no line of
`gen_tool_packages.py`'s manifest (it resolves RE tools from `tool-catalog.json`, which has
no desktop-theme entries) installs `papirus-icon-theme` anywhere in this diff. Repo-wide
`git grep -i papirus` at `ffd0282` returns exactly one hit — the greeter config that
*references* it.

Compounding this, `limpet/iso-builder/bloat-removal.list:18-21` reads:

```
# Games bundled by the xubuntu-desktop metapackage.
xubuntu-icon-theme
gnome-mahjongg
gnome-mines
gnome-sudoku
```

`xubuntu-icon-theme` is not a game. Per Launchpad's own package listing
(`xubuntu-artwork` source package), it is "Xubuntu icon theme (transitional package)" —
the icon-theme metapackage the stock Xubuntu desktop install pulls in by default. `stage2`
(`build.sh:65-76`) purges every package on this list via `chroot ... apt-get purge -y`,
then `apt-get autoremove -y`. So the one icon theme a freshly-extracted base ISO *does*
carry gets actively removed, under a comment that misclassifies it, while the theme the
greeter config asks for is never installed to replace it.

Net effect on a real build: after `stage2` + `stage9`, the chroot has no
`papirus-icon-theme` and no `xubuntu-icon-theme` either. LightDM boots with
`icon-theme-name = Papirus-Dark` pointed at nothing; GTK falls back to whatever stub/hicolor
icons ship with the toolkit itself. This is exactly the "branding" task (wq#3946 task 9,
"Branding assets... LightDM greeter theme") the PR's own stage9 claims to deliver, and
exactly the surface (`overlay/etc/lightdm/`) the PR's own README (`## The ticket cites a
spec file that does not exist`, bullet 2) cites as evidence it *did* follow
`docs/branding-spec.md`. The README's extensive "Interpretation decisions this script
makes (none guessed silently)" section — which correctly catalogs six other real gaps
(installer wizard mapping, precheck service, bloat list provenance, RE tool packaging,
GRUB/Plymouth, hardware-book count) — does not mention this one. It isn't a documented
tradeoff; it's a self-contained inconsistency between two files this same diff ships.

This is distinct from every other "known gap" in the PR (which are all genuinely upstream —
a spec file that was never written, an executable a different ticket owns). Here the spec
file exists, names the exact package, and the diff's own greeter config quotes that exact
package name — the diff just never installs it, and actively removes the one fallback that
was present.

## Secondary finding (minor, non-blocking): test files ship into the production chroot

`stage3_inject_limpet_packages` (`build.sh:92-93`):

```
mkdir -p "$CHROOT/opt/limpet/lib"
cp -a "$LIMPET_ROOT"/*.py "$CHROOT/opt/limpet/lib/"
```

`limpet/*.py` at `ffd0282` is 14 files: 7 real modules (`__init__.py`, `cli.py`,
`daemon.py`, `health.py`, `manifest.py`, `registry.py`, `registry_core.py`) and 7 pytest
suites (`test_limpet_brand.py`, `test_limpet_cli.py`, `test_limpet_daemon.py`,
`test_limpet_health.py`, `test_limpet_manifest.py`, `test_limpet_registry.py`,
`test_limpet_registry_core.py`). The unqualified glob ships all 14 into the shipped
appliance's `/opt/limpet/lib` — dev/test artifacts baked into the production image
alongside the code they test. Not a crash, not scoped by any spec either way; a hygiene
defect a one-line glob exclusion (or an explicit file list) fixes.

## Scope check

ISO assembly (what this ticket claims) vs. desktop *provisioning* (setting the running
system's live xfconf `/Net/IconThemeName` key, which `branding-spec.md:168` attributes to
"the provisioning ticket", a separate concern from package installation) are different
things. This finding is scoped to package installation only — baking `papirus-icon-theme`
into the chroot and not purging its only fallback — which is squarely build-pipeline work
(`build.sh` tasks 2/3/9), not provisioning. Whether a later provisioning ticket also needs
to set the XFCE session's own `/Net/IconThemeName` key is a separate, unverified question
this report does not claim either way.

## Conclusion

CONTRADICT. Escalating per archetype 8 policy (fresh sonnet-5@high verifier, same model as
author → escalate opus-5@high on contradiction). Filed `[judgment]` follow-up ticket for
disposition (reopen wq#3946 vs. forward-fix) — see wq ticket list for the id. Recommended
fix shape for that ticket: add `papirus-icon-theme` to the chroot package-install step
(stage3 or a new line in stage4/9), and move `xubuntu-icon-theme` out of
`bloat-removal.list`'s games block (either drop the line entirely, or — if removing the
stock theme after the replacement lands is actually wanted — re-home it under a comment
that says so, ordered after the `papirus-icon-theme` install).
