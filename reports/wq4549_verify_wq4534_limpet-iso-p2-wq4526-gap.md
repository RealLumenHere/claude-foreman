# wq#4549 verify of wq#4534 — live ISO boot menu serial console (closes wq#4526 gap)

- PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/2393 (merged to main as `d7a9c59f`)
- Author model: claude-sonnet-5. Verifier: claude-sonnet-5@high, fresh instance.
- Verdict: **CONFIRM**. The fix is correct and its new tests are falsifiable. One
  pre-existing test-coverage gap found (non-blocking, documented below); no
  escalation to opus-5@high.

## What the PR claims

wq#4526 set `console=ttyS0,115200n8` in the chroot's `/etc/default/grub` (stage10,
via `set_grub_cmdline.py`) — that's the *installed* system's grub config. stage12's
QEMU boot test boots the live ISO directly (`-cdrom`), never an installed system, so
that fix never touched the thing actually being tested: the live ISO's own
`boot/grub/grub.cfg` / `loopback.cfg`, which `stage11_build_hybrid_iso` copies
verbatim from the base ISO with kernel args untouched. wq#4534 adds
`add_serial_console()` to `iso_layers.py`, wired into the `grub-cfg` subcommand
(already called by stage11 to rebrand menu entries), inserting
`console=tty0 console=ttyS0,115200n8` into every `linux ... ---` kernel line in
both files. `tty0` first / `ttyS0` last is deliberate: the kernel's *last*
`console=` arg becomes `/dev/console`, so the local display stays live while
`ttyS0` stays primary for the serial-based boot test — this is correct kernel
console semantics.

## What I checked

- Read every changed file's diff directly off `main` (`git show d7a9c59f`):
  `iso_layers.py`, `set_grub_cmdline.py`, `build.sh`, `README.md`,
  `test_layered_remaster.py`, `test_set_grub_cmdline.py`.
- Ran the full relevant test suite against the shipped code, unmodified:
  `python3 -I -m pytest test_layered_remaster.py test_set_grub_cmdline.py` →
  **26 passed**, including the 5 new tests (`test_grub_cfg_adds_serial_console_to_every_linux_line[grub.cfg|loopback.cfg]`,
  `test_grub_cfg_serial_console_covers_nomodeset_line`,
  `test_grub_cfg_serial_console_is_idempotent`,
  `test_grub_cfg_serial_console_fails_loudly_on_unknown_kernel_line`).
- Manually drove `add_serial_console()` against adversarial inputs not in the
  shipped tests (no space before `---`, tabs instead of spaces, two independent
  `menuentry` blocks, a line already carrying a *different* `console=ttyS0,9600`
  value simulating manual tamper). All behaved as the implementation's own
  comments claim: idempotent on exact match, inserts cleanly otherwise, never
  touches the `quiet splash` tail after `---`. No ReDoS risk in the
  `(?:\s+\S+)*?` lazy group at this input size/shape.
- Checked for the obvious `linuxefi`/legacy-GRUB blind spot (the regex only
  matches a bare `linux` token, not `linuxefi`): the real fixture
  (`fixtures/xubuntu-26.04/boot/grub/{grub.cfg,loopback.cfg}`, copied from the
  actual base ISO) uses plain `linux` for both BIOS and UEFI boot via grub2 —
  there is no `linuxefi` entry in the real artifact this ships against, so this
  is not a live gap, just a theoretical one for a different bootloader family.
- Confirmed `build.sh` stage11's `for cfg in grub.cfg loopback.cfg; do install
  ...; iso_layers grub-cfg --file ...; done` (lines 701-704) actually calls the
  same CLI the unit tests exercise, with no divergent code path.

## Finding: pre-existing end-to-end test not strengthened for this fix (non-blocking)

`test_iso_grub_cfg_emitted_and_rebranded` (test_layered_remaster.py:363) is the
only test that runs through the *real* `stage11_build_hybrid_iso` shell function
(via the `remaster` fixture, which genuinely sources and executes `build.sh`,
not a stub) and inspects the actual emitted ISO's `grub.cfg`/`loopback.cfg`. It
was left unchanged by this PR and only asserts the rebrand
(`menuentry "Try or Install Limpet"` and the literal `linux  /casper/vmlinuz`
substring) — it never asserts `console=ttyS0` is present in the real output.

I verified this is a real, demonstrable gap, not a theoretical one, with two
targeted probes against my own worktree copy (each reverted with `git checkout --`
immediately after observing results; worktree confirmed clean before writing
this report):

1. Removed the `iso_layers grub-cfg --file ...` call from `build.sh`'s stage11
   loop entirely (full regression to pre-wq#4534 behavior). Result: this one
   end-to-end test correctly fails — but only because the *rebrand* assertion
   fails, not because of any serial-console assertion.
2. Restored `build.sh`, then removed only the `add_serial_console(...)` call
   from `iso_layers.py`'s `grub-cfg` CLI handler (kept `rebrand_grub`). Result:
   all 5 new unit tests fail immediately (as designed — they call the CLI
   directly), but `test_iso_grub_cfg_emitted_and_rebranded` still **passes**,
   because rebranding alone satisfies its only assertions.

So a future regression that breaks serial-console injection specifically while
leaving rebranding intact (e.g., someone refactors the `grub-cfg` handler and
drops one line, or stage11's wiring silently calls a stale cached module) would
pass the one test that exercises the real, fully-assembled build pipeline, and
only get caught by the narrower unit tests that call `iso_layers.py` directly —
which is still coverage, just not through the integration point the whole
ticket (wq#4526 → wq#4534) is about. Recommend (not blocking this verify) adding
`assert "console=ttyS0" in text` to `test_iso_grub_cfg_emitted_and_rebranded` in
a follow-up.

## Conclusion

The fix is correct: kernel console-arg ordering rationale checks out, the regex
and injection logic handle every case in the real fixture plus the adversarial
cases I added, idempotency holds, and the `die()` guard fires on an unrecognized
layout. All 26 tests pass unmodified. The one gap found is in test depth at the
integration-test layer, not in the shipped behavior — does not contradict
wq#4534's claim and does not meet the bar for escalation.
