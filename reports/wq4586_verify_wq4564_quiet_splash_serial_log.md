# wq#4586 verify of wq#4564 — drop `quiet`, add `loglevel=7` for live-ISO serial log

- Commit: `605fb70b` on this branch (`fix(iso-builder): wq4564 drop quiet, add loglevel=7
  for live-ISO serial log`), Refs wq#4564, logged at ISSUE-LOG.md row 1555.
- Author model: claude-sonnet-5. Verifier: claude-sonnet-5@high, fresh instance.
- Verdict: **CONFIRM**. The fix is correct, its tests are falsifiable, and I found no
  gap that contradicts the shipped claim. No escalation to opus-5@high.

## What the PR claims

wq#4534 (verified clean by wq#4549) added `console=tty0 console=ttyS0,115200n8` to
every `linux ... ---` line in the live ISO's own `boot/grub/{grub.cfg,loopback.cfg}`,
fixing serial *routing* for stage12's QEMU boot test. But both files still ship
`quiet splash` in the tail after `---`, and `quiet` lowers `console_loglevel`
fleet-wide — every console, including the now-primary ttyS0 — so the kernel/systemd
printed almost nothing regardless of where it was routed, leaving
`limpet-serial.log` near-empty (wq#4564, ISSUE-LOG row 1555). The fix: drop `quiet`,
keep `splash` (Plymouth owns only the tty0 framebuffer, never touches what printk
sends to ttyS0 — dropping it instead would cost wq#3948's graphical boot branding
for zero serial-log gain), and add `loglevel=7 systemd.show_status=1` in its place.
`add_serial_console()` (`limpet/iso-builder/iso_layers.py:109-123`) now rewrites both
halves of the `linux` line in one pass: console args before `---` (unchanged from
wq#4534), verbosity args replacing `quiet` in the tail after it.

## What I checked

- Read the full diff cold (`git show 605fb70b`): `iso_layers.py`, `README.md`,
  `test_layered_remaster.py`. No other files touched; `build.sh`'s single call site
  (`iso_layers grub-cfg --file ...`, line 706) is unchanged and still the only caller.
- Confirmed the real fixture shape this ships against
  (`fixtures/xubuntu-26.04/boot/grub/{grub.cfg,loopback.cfg}`, copied verbatim from
  the base ISO) has exactly the `--- quiet splash` tail the fix assumes, on all 4
  `linux` lines across both files (2 per file, including the `nomodeset` variant).
- Ran the full suite unmodified: `python3 -I -m pytest test_layered_remaster.py` →
  **22 passed**, including the 2 new/strengthened assertions
  (`test_grub_cfg_adds_serial_console_to_every_linux_line[grub.cfg|loopback.cfg]`
  now asserts `tail == f"splash {VERBOSE_ARGS}"` and `"quiet" not in tail`,
  `test_grub_cfg_serial_console_is_idempotent` now also asserts
  `second.count("loglevel=7") == 2`).
- Drove `add_serial_console()` against adversarial inputs not in the shipped tests
  (script kept at `/tmp/fworker-wq4586/probe_edges.py`, not committed):
  - `quietly splash` → word-boundary regex (`\bquiet\b`) correctly leaves `quietly`
    alone, confirming no over-matching on substrings.
  - `splash quiet` (quiet as the last/only trailing token, no following word) →
    stripped cleanly (cosmetic double-space artifact only, no functional defect).
  - tail with no `quiet` at all, and an empty tail (`--- ` with nothing after) →
    both handled without error, `verbose_args` appended either way.
  - Two independent `quiet` tokens in one tail (contrived — real fixture never ships
    this) → only the first is stripped (`count=1`), so a hypothetical doubled-`quiet`
    layout would silently leave one `quiet` behind and partially defeat the fix.
    Judged non-blocking: no real Xubuntu/Debian grub.cfg ships `quiet` twice in one
    kernel line, the real fixture has exactly one, and `die()` still fires on any
    `---`-less layout change per the existing loud-failure test.
- Confirmed idempotency holds under the new code too: `loglevel=` presence in `tail`
  gates the whole quiet-strip-and-append block, so a second run is a true no-op on
  the tail (test asserts `second == first` byte-for-byte plus the explicit
  `loglevel=7` count).
- Checked for scope creep / missed sibling files: no `isolinux`/`syslinux`
  BIOS-boot config exists in the fixture set (Xubuntu 26.04 is grub2-only here), so
  there is no second boot-config family this fix should have touched but didn't.
  `grep`-ed the repo for other consumers of `quiet splash`/`VERBOSE_ARGS`/`loglevel=7`
  outside the three changed files — the only other hit is
  `test_set_grub_cmdline.py`, which covers the *installed* system's
  `/etc/default/grub` (wq#4526, a separate code path, untouched and correctly out of
  scope here).
- Re-confirmed the pre-existing, already-logged (wq#4549) non-blocking gap is still
  present and unchanged by this PR: `test_iso_grub_cfg_emitted_and_rebranded`
  (`test_layered_remaster.py:377`), the one test that runs the real
  `stage11_build_hybrid_iso` shell path end-to-end, still only asserts the rebrand
  and does not assert `console=ttyS0`/`loglevel=7` presence. This is not a new gap
  introduced by wq#4564 and does not contradict its claim — carrying it forward as a
  standing non-blocking note, same disposition as wq#4549.

## Conclusion

The reasoning holds: `quiet` genuinely lowers `console_loglevel` on every console
including the serial one, `splash`/Plymouth genuinely never touches ttyS0 printk,
and GRUB's left-to-right cmdline parsing genuinely means the appended
`loglevel=7 systemd.show_status=1` wins over any earlier default. The regex change
is boundary-safe (word-boundary match, idempotent via the `loglevel=` tail check,
loud `die()` on unrecognized layout), all 22 tests pass unmodified, and my own
adversarial probes found only one cosmetic artifact and one contrived
not-in-the-real-fixture edge case, neither of which rises to a contradiction.
