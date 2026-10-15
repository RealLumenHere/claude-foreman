# wq#3931 — verify pass of wq#3924 (LIMPET RE hardware driver catalog)

**Verdict: CONFIRM** (one minor citation-path defect, not blocking).

Author: claude-sonnet-5 (fworker-wq3924). Verifier: claude-sonnet-5@high (this session,
fresh instance, no re-use of author reasoning).

## What was shipped (wq#3924, commit `aefae655`, squash of PR branch commit `8180b487`,
already merged to main — ancestor of this verify worktree's HEAD)

- `design/wq3924_hardware_driver_catalog.md` (101 lines) — scope note, evidence-posture
  disclosure (fleet-search web down), per-device field schema, build-script summary,
  4 cross-cutting findings.
- `limpet/hardware.d/{01..12}-*.toml` — 12 category files, 42 `[[device]]` entries total.
- `limpet/hardware.d/build_iso_drivers.py` (96 lines at this commit) — pure
  `load_catalog`/`dkms_targets`/`firmware_targets`/`render_report`, no filesystem writes,
  no subprocess calls.
- `limpet/hardware.d/test_build_iso_drivers.py` (4 tests at this commit).

Note: `build_iso_drivers.py`/its test file grew further (write_bundle, udev_rules,
proprietary_report, 3 more tests) under the separate, later wq#3941 ticket — out of scope
for this verify pass; confirmed via `git diff aefae655 HEAD -- <those files>` that the
design doc is untouched since wq#3924's own commit and still accurately describes only
the 2-list/4-test scope it shipped.

## Checks run

1. **Tests pass.** `python3 -m pytest limpet/hardware.d/test_build_iso_drivers.py` → 7/7
   passing at HEAD (includes wq#3941's 3 additions); confirmed via
   `git show aefae655:limpet/hardware.d/test_build_iso_drivers.py | grep -c '^def test_'`
   that wq#3924 itself shipped exactly 4, matching the design doc's "4/4 passing" claim.
   The 12 `.toml` catalog files are unchanged since `aefae655` (`git diff --stat` empty),
   so this run is valid evidence for wq#3924's own tests, not just current HEAD's.

2. **Device count matches the claim.** Parsed all 12 TOML files with `tomllib` directly
   (not via the script, to avoid trusting the thing under test) → 42 `[[device]]` entries,
   exactly matching "42 device entries total" in the design doc.

3. **Build script output is sane.** Ran `build_iso_drivers.py` directly: 1 DKMS target
   (rtl8812bu, ~3 min) and 8 firmware/package targets, consistent with the catalog content
   and the design doc's §2 summary (rtl8812bu as "currently exactly one" DKMS entry).

4. **Cross-reference to an existing plugin example, spot-checked.** The catalog's
   `09-sdr.toml` HackRF entry claims `usb_ids = ["1d50:6089", "1d50:cc15"]`, "cross-checked
   against the existing `limpet/examples/hackrf.toml`." Read that file directly:
   `[hardware] usb_ids = ["1d50:6089", "1d50:cc15"]` — exact match.

5. **"No VID:PID by design" claims.** Design doc §0 says exactly two devices (Bus Pirate 6,
   GlitchKit) are shipped with no VID:PID rather than a guessed one. Grepped both: Bus
   Pirate 6 (`03-multiprotocol-tools.toml`) and GlitchKit (`11-voltage-glitching.toml`) both
   have `usb_ids = []`, both with per-device prose explaining why a stable ID can't exist
   here. Matches.

6. **Prior-art outage claim, reproduced live.** Ran `fleet-search web "test query status
   check"` myself just now (2026-10-15) — identical failure mode to what the doc describes:
   all four general engines suspended/CAPTCHA'd, fallback failing on "no BRAVE_API_KEY
   configured (see wq#2595)". `kb/ISSUE-LOG.md` row 715 independently corroborates the same
   outage from a 2026-09-25 verify pass, and the sibling same-day doc
   `design/wq3900_limpet_vision_component_id.md` makes the identical claim about the
   identical date (2026-10-14). Not a Tier-0-instinct-8 fabrication: the outage is real,
   reproducible, and pre-logged — the author didn't invent the excuse.

7. **Spot-checked ~20 of 42 device entries against known Linux driver history**
   (USB-serial: ftdi_sio/cp210x/ch341/pl2303 and their in-kernel-since claims; flash
   programmers: CH341A SPI mode vs. UART mode module distinction; debug probes: J-Link/
   ST-Link/CMSIS-DAP/Black Magic Probe VID:PIDs and driver paths; AI accelerators: Coral USB
   dual VID:PID boot-sequence claim; network adapters: ax88179_178a since 3.8, r8152 since
   3.16, rtl8812bu out-of-tree; SDR: RTL-SDR's `dvb_usb_rtl28xxu` auto-claim/blacklist need,
   HackRF dual-mode PIDs; HDMI capture and FPGA programmers, including the Xilinx EULA
   licensing-blocker flag). All technically correct and consistent with the `confidence`
   field assigned to each (low-confidence entries are the ones a human would also flag:
   batch-variable MS2109/MS2130 clone PIDs, Saleae's undocumented current-gen VID, YARD
   Stick One/LimeSDR PIDs not live-verified).

8. **No credential-shaped strings.** Grepped all shipped `.toml`/`.md` files for
   TOKEN/SECRET/PASSWORD/AKIA/ghp_/sk-ant patterns — none found.

## Defect found

**Broken cross-reference path.** `design/wq3924_hardware_driver_catalog.md:7` and `:82`
cite `design/target-wizard-spec.md §4.2` for the HAL's VID:PID resolution order. That path
does not exist — the real file is `docs/target-wizard-spec.md` (confirmed: four other
in-repo references — `TOOL_REGISTRY.md:301`, `docs/settings-spec.md:194`,
`limpet/iso-builder/README.md:37`, `limpet/iso-builder/build.sh:105` — all consistently use
the `docs/` path; `design/target-wizard-spec.md` is a 404). Read the real file's §4.2
("IDENTIFY — the known/unknown fork", `docs/target-wizard-spec.md:138`) directly: it does
describe a weighted VID:PID resolution order exactly as the catalog doc summarizes it, so
this is a pure directory-name typo (`design/` vs `docs/`), not a wrong section number or a
fabricated claim about what §4.2 contains.

Severity: cosmetic. Nothing in the shipped TOML catalog, the HAL's actual consumption path,
or `build_iso_drivers.py` depends on this citation resolving — it's a dead link in prose
only. Does not change any technical conclusion in the catalog and does not meet the bar for
a contradiction/opus-5@high escalation.

## Verdict

The catalog data, build script, and tests are all independently verified correct: device
count matches, cross-checked VID:PIDs match an existing plugin file, the "down fleet-wide"
excuse for unverified entries is reproducible right now, and ~20 spot-checked entries line
up with known Linux driver history, with low-confidence entries honestly flagged as such
rather than guessed. One broken doc path (`design/` should read `docs/` for
`target-wizard-spec.md`, two occurrences) is the only defect found — reference hygiene, not
a substantive error. **CONFIRM.**

Worth a one-line fix if anyone touches this doc again (not worth a new ticket on its own):
- `design/wq3924_hardware_driver_catalog.md:7` and `:82`:
  `design/target-wizard-spec.md` → `docs/target-wizard-spec.md`.
