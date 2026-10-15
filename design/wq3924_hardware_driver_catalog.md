# wq#3924 — RE hardware driver catalog (in-kernel vs DKMS vs firmware blobs for ISO)

Compiles, for every device category the ticket names, what Linux needs to drive it:
in-kernel module, out-of-tree DKMS build, firmware blob, udev rule, VID:PID, and any
proprietary-tooling limitation. Feeds the Limpet ISO build (which DKMS modules to
pre-compile, which firmware packages to bundle) and the HAL/install-wizard hardware
manifests (`design/target-wizard-spec.md` §4.2's VID:PID resolution order).

## 0. Constraints and evidence posture (read first)

**Prior-art search ran degraded.** The mandatory fleet-search step was attempted first
(`fleet-search web`, `github`, `reddit`) — web is fully down fleet-wide (all SearXNG
general engines suspended/CAPTCHA, no Brave fallback key, pre-existing wq#2595; same
outage `design/wq3900_limpet_vision_component_id.md` hit the same day), github returned
401 (no usable token), reddit responded but empty for the queries tried. This is a
known, already-logged infrastructure gap (`kb/ISSUE-LOG.md` row 715+), not something
this ticket can fix, and not evidence of a fleet problem (Tier-0 instinct #8 doesn't
apply here — no fabricated value was queried; the *tool* is down, confirmed by it
failing identically on an unrelated query).

Every device entry in the catalog below therefore carries a `confidence` field:
- `high` — long-stable, widely-documented driver history (most USB-serial chips,
  in-kernel network/video classes, open-hardware probes). Safe to trust for silent
  auto-detection.
- `medium` — correct in shape, but a specific VID:PID/package name/kernel-version
  threshold should get a live check before the ISO build locks it in.
- `low` / `unverified` — explicitly flagged in-file. Two entries (Bus Pirate 6,
  GlitchKit) have **no VID:PID at all** by design: inventing one would violate the
  "never guess a path, model name, config value, address" rule and would also set up
  a future wq#3924-fabricated-evidence incident per Tier-0 instinct #8. Route these
  through the ticket's own stated fallback (online search once it's back, or USB
  sideload) instead.

Treat every `medium`/`low`/`unverified` entry as a **[verify-before-ISO-lock]** item:
cheap to confirm once fleet-search is back, expensive to ship wrong in a bootable image.

## 1. Where the catalog lives

`limpet/hardware.d/*.toml` — 12 files, one per ticket category, 42 device entries
total. Not a `limpet-plugin.toml` (`design/limpet/plugin-manifest.schema.json`): a
plugin manifest declares one *active* HAL binding (`category=hardware`, one
`hardware.detection.usb_vendor`/`usb_product` pair); this catalog is reference data
about *every known device in a category*, independent of whether any plugin for it is
currently installed. The two are complementary: when a device here gets a plugin
written for it (like the existing `limpet/examples/hackrf.toml`), that plugin's
`[hardware]` block should match this catalog's `usb_ids` for the same device — verified
directly for HackRF (09-sdr.toml cross-checks against `limpet/examples/hackrf.toml`).

Per-device fields (direct mapping to the ticket's requested output):
`name`, `category`, `usb_ids`, `linux_driver`, `in_kernel_since`, `firmware_blob`,
`firmware_package` (+ `firmware_note` for the rare cases where the package name alone
doesn't carry the licensing/delivery nuance — see §2), `dkms`, `dkms_source`,
`dkms_build_estimate_minutes`, `udev_rule`, `proprietary_notes`, `confidence`.

## 2. Build script: what the ISO needs pre-baked

`limpet/hardware.d/build_iso_drivers.py` (tested: `limpet/hardware.d/test_build_iso_drivers.py`,
4/4 passing) parses every `*.toml` in the directory and derives two lists — it does not
duplicate them by hand, so the plan can't silently drift from the catalog:

- **DKMS modules to pre-compile**: every `dkms = true` entry. Currently exactly one:
  `rtl8812bu` (Realtek RTL8812BU USB WiFi), ~3 min estimated build time, source
  `github.com/morrownr/8812bu-20210629`. This is the one category-10 chip with *no*
  mainline equivalent — everything else in the network-adapters category is in-kernel.
- **Firmware/package targets to bundle**: every `firmware_blob = true` entry with a
  real package name, deduplicated by package. Splits into `linux-firmware` (MT7921U),
  tool-bundled blobs that ship inside a userspace package rather than `/lib/firmware`
  (`sigrok-firmware-fx2lafw` for DSLogic/FX2 clones, `libedgetpu` for Coral, `LimeSuite`
  for LimeSDR, `glasgow` for the Glasgow Interface Explorer, the RTL8812BU driver's own
  bundled rtlwifi blobs), and one proprietary/EULA-gated blob (`xusbdfwu.hex` for Xilinx
  Platform Cable USB — see §3).

Run it: `python3 limpet/hardware.d/build_iso_drivers.py` → prints the plan; the actual
ISO build step (outside this ticket's scope — blocks on the ISO-build ticket this one
unblocks) wires its ouput into whatever packages/DKMS-prebuild step that pipeline uses.

## 3. Cross-cutting findings worth carrying into the ISO build / install wizard

1. **VID:PID is not always enough to identify a device.** Several categories reuse a
   bare chip's generic identity: a Bus Pirate v3, a JTAGulator, and a plain FT232R all
   report `0403:6001`; a Tigard and a bare FT2232H (including iCE40 dev boards) both
   report `0403:6010`. The HAL's resolution order (`design/target-wizard-spec.md` §4.2)
   needs a tier below VID:PID match — a protocol probe or USB string-descriptor check —
   for these, not just a table lookup. Flagged per-device in `03-multiprotocol-tools.toml`
   and `12-fpga-programmers.toml`.
2. **RTL-SDR needs an active unbind, not just a missing driver.** The in-kernel
   `dvb_usb_rtl28xxu` DVB-T driver auto-claims these dongles before `librtlsdr` can; the
   ISO needs a `modprobe.d` blacklist shipped by default, not merely the udev rule.
   (`09-sdr.toml`)
3. **One device has a real licensing blocker, not just a technical one.** The Xilinx
   Platform Cable USB's firmware blob (`xusbdfwu.hex`) ships inside Xilinx's ISE/Vivado
   installer under a restrictive EULA with no general-redistribution grant. This is a
   go/no-go question for whoever owns the ISO's license posture, not something to solve
   by reverse-engineering around it — flagged as the one `[physical/legal task]` in this
   catalog per Tier-0 instinct #3 (not a Frank-gate on the rest of the ticket, just a
   queued decision on that one device). (`12-fpga-programmers.toml`)
4. **USB webcams and UVC HDMI dongles need zero catalog entries to work**, by design —
   the UVC/UAC device classes have been in-kernel since 2.6.26 and cover both. The
   catalog still lists them (ticket asked for every category) but the finding itself is
   "this category needs nothing special," which matters for the ISO build's effort
   budget as much as the categories that need real pre-bake work.
