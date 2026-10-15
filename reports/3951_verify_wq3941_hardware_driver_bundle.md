# wq#3951 — Adversarial verify-pass of wq#3941 (RE hardware driver bundle, PR#1903)

**Verdict: CONTRADICT** — `build_iso_drivers.py`'s own logic (`dkms_targets`,
`firmware_targets`, `udev_rules`, `proprietary_report`, `write_bundle`) is correct, well
tested, and does what the ticket's task list (1-5) and the author's done notes claim. But
the diff's new module docstring cites a file, `limpet/hardware.d/README.md`, that does not
exist anywhere in the repo. This is the exact fabricated-doc-pointer pattern already logged
twice for this ticket family (`kb/ISSUE-LOG.md` rows 1327, 1329: `install-wizard-spec.md`
section 6), reintroduced here at smaller scale, inside the fix itself.

## What was shipped (PR#1903, commit 6c880864, real diff vs merge-base `5992ca6`)

Only two files changed: `limpet/hardware.d/build_iso_drivers.py` (+115/-8) and
`limpet/hardware.d/test_build_iso_drivers.py` (+28 new). (A naive `git diff main..PR`
shows hundreds of unrelated deletions — that's `main` having advanced past the PR's base,
not this PR's content; diffed against the actual merge-base the change is exactly these two
files.)

`build_iso_drivers.py` gained:
- `udev_rules(devices)` — consolidated `90-limpet-hardware.rules` text from each device's
  `udev_rule` catalog field, skipping empty rules and dropping each device's own
  standalone-file comment.
- `proprietary_report(devices)` — consolidated proprietary/closed-source limitations doc,
  skipping devices whose `proprietary_notes` is empty or literally starts with `"none."`.
- `write_bundle(devices, bundle_dir)` — writes all three artifacts (udev rules, prebake
  plan, proprietary notes) into a caller-given directory.
- `--write-bundle DIR` CLI mode wired to `write_bundle`.

`test_build_iso_drivers.py` gained 4 new tests covering the above.

## Probe 1: run the shipped test suite — passes

Checked out the PR branch in a scratch worktree (`/tmp/fworker-wq3951/pr1903-check`, full
catalog present) and ran it standalone:

```
$ python3 -m pytest test_build_iso_drivers.py -v
...
7 passed in 0.06s
```

All 7 tests pass, including the 4 new ones.

## Probe 2: run the CLI for real

```
$ python3 build_iso_drivers.py --write-bundle /tmp/fworker-wq3951/bundle-out
udev_rules: /tmp/fworker-wq3951/bundle-out/90-limpet-hardware.rules
prebake_plan: /tmp/fworker-wq3951/bundle-out/prebake-plan.md
proprietary_notes: /tmp/fworker-wq3951/bundle-out/PROPRIETARY-LIMITATIONS.md
```

All three files generated with real, correctly-filtered content (e.g. CH341A's udev rule
present, ASIX AX88179 absent from both the rules file and the proprietary report since its
notes are `"None. Fully open, in-kernel."`).

## Probe 3: adversarial data-edge cases against the live catalog — no crash found

- `dkms_targets()` indexes `d["linux_driver"]` unconditionally for every `dkms=true`
  device. Checked every `dkms = true` entry in `limpet/hardware.d/*.toml` (only one exists,
  RTL8812BU) — `linux_driver` is set, no `KeyError`.
- `firmware_targets()` filters on `firmware_package != "none"` before indexing
  `d["firmware_package"]`, so a device with `firmware_blob=true` and no package key can't
  crash it (excluded, not indexed). Checked all 8 `firmware_blob = true` entries in the
  catalog — all carry a real package name, none hit this path either way.
- `proprietary_report()`'s `note.lower().startswith("none.")` filter correctly
  distinguishes a true "no caveat" clearance (`"None. Fully open, in-kernel."`) from a
  caveat that happens to start with the word "None" but isn't one (`"None on Linux. ..."`,
  `"None for UART mode. ..."` — both fail the `"none."` prefix check since the next
  character is a space, not a period, so both correctly survive into the report). Verified
  by reading every `proprietary_notes` value in the catalog, not just the two the shipped
  tests exercise.

None of these are bugs — the code holds up against the actual catalog data on disk.

## The contradiction: a citation to a file that was never created

`build_iso_drivers.py`'s new docstring (line 22-24 of the PR version) reads:

> "...is the ISO build script's job (mkosi/live-build hook) once that pipeline exists; see
> `limpet/hardware.d/README.md` for current status (no base image/pinned target kernel yet,
> tracked wq#3941 follow-up)."

- `find /home/lumen/worktrees/wq3951-a2 -iname "README.md" -path "*hardware*"` (repo-wide,
  via a scratch checkout of the full PR tree) → no match anywhere under `limpet/hardware.d/`.
- Diffed against the merge-base (`5992ca64`): the pre-PR docstring has no `README.md`
  reference at all — this citation is new in this diff, not inherited from wq#3924.
- `wq list` / `wq show 3941` → no follow-up ticket exists to create this file. wq#3941's own
  done notes say "No ISO build script/install-wizard-spec.md/pinned kernel exists yet (see
  `kb/ISSUE-LOG.md` row 1327)" — i.e. the author was aware of and explicitly flagged this
  exact failure mode (citing docs that don't exist) for the *ISO pipeline*, then reintroduced
  a smaller instance of the identical mistake in the *module's own docstring* within the same
  diff.

A future reader (human or agent) following "see `limpet/hardware.d/README.md` for current
status" finds nothing. This is the Tier-0 "fabricated evidence" failure mode (an introduced
path that was never verified to exist) landing inside the exact ticket family (`kb/ISSUE-LOG.md`
rows 1327, 1329) already logged twice for this precise anti-pattern.

## Disposition

Functionally the PR is sound: the generator code is correct, the tests are real (not
tautological — `test_write_bundle_writes_all_three_files` would catch a wiring mistake like
writing the wrong function's output to the wrong path, since the three functions produce
materially different content), and the CLI produces valid output against the live catalog.
The defect is narrow: one dangling doc citation. But it is a genuine, verifiable false
statement shipped as "done," and it is the same class of error this ticket's own research
chain has already been burned by twice. Filing as a judgment ticket per archetype-8 protocol
rather than waving it through: the opus-5@high judgment call is whether to (a) strip the
`README.md` reference now (cheap, no information lost — the surrounding sentence already
states the real status inline), or (b) actually create
`limpet/hardware.d/README.md` with that status note so the citation becomes true.
