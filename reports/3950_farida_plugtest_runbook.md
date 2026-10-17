# wq#3950 — Farida plug-test runbook + remote-validation status (attempt 2)

**Status: remote prep re-validated and extended; the plug-in step itself remains a
physical act this sandbox cannot perform.** This is attempt 2 (fence=2). Attempt 1
(`iso-build-f85923`, notes on wq#3950) already did the reachable engineering work: found
and fixed a real bug in the generator (udev rules failed `udevadm verify` because the
RTL-SDR entry's `modprobe.d` blacklist lines were pasted into the rules body), merged as
PR#2038 / commit `b6a08a99f8` (squashed into `main` as `7ba9dbce`,
`limpet/hardware.d/build_iso_drivers.py:38-43,125-139`), and left a self-contained
plug-test kit at `/home/lumen/projects/limpet/iso-build/farida-plugtest-kit.tgz` (outside
this repo and outside this worker's allowed dirs, so not re-verified from this session).

This attempt re-ran the reachable checks against current `main` (catches drift the kit
can't, since the kit is a frozen snapshot) and found one new gap attempt 1 did not flag:
**HAL has no detection plugin at all for 5 of the ticket's 12 categories**, independent of
the already-known notify-wiring gap. Both gaps are below, by category.

## Re-validation this attempt (no hardware, no SSH — Lumen-local only)

```
$ python3 -m pytest limpet/hardware.d/test_build_iso_drivers.py -v
...
11 passed in 0.15s
```
includes `test_generated_rules_pass_udevadm_verify` — the attempt-1 fix still holds against
the live catalog (see `limpet/hardware.d/test_build_iso_drivers.py:97-101` for the
`udevadm verify` subprocess check itself).

```
$ python3 -m pytest ops/wq3809-limpet-hal/tests/test_hal.py -q
17 passed in 0.20s
```
`hal.py detect`'s plugin-matching logic (vendor-string false-positive guards per wq#3814/
wq#3819) is sound on its own test fixtures.

```
$ python3 limpet/hardware.d/build_iso_drivers.py --write-bundle <dir>
udev_rules: .../90-limpet-hardware.rules
prebake_plan: .../prebake-plan.md
proprietary_notes: .../PROPRIETARY-LIMITATIONS.md
modprobe_conf: .../90-limpet-hardware-modprobe.conf
```
regenerated cleanly from the current catalog (12 files, 43 `[[device]]` entries).

Nothing in this session touched Farida or any hardware: SSH to Farida
(`ssh farida@100.85.126.30`, tailscale IP per `lumen_state.py current farida`) is not on
this worker's Bash allowlist (`/home/lumen/foreman/harness/worker-settings.json` has no
`ssh` entry at all — not a path-scope refusal, a missing-capability one), so installing
the rules/DKMS modules onto Farida must happen from a session or operator with that reach,
not from this one.

## Category matrix: ticket's 12 categories vs. catalog vs. `hal.py` detection coverage

`hal.py` (`ops/wq3809-limpet-hal/hal.py:171-269`, `REGISTRY`) has exactly 8 `hw_type`
plugins. Cross-referencing against the ticket's 12 physical-test categories:

| Ticket category | Catalog file (device count) | `hal.py` plugin | Gap |
|---|---|---|---|
| flash-programmer | `01-flash-programmers.toml` (3) | `flash_programmer` | detect OK |
| debug-probe | `02-debug-probes.toml` (5) | `debug_probe` | detect OK |
| multiprotocol | `03-multiprotocol-tools.toml` (7) | `bus_interface` | detect OK |
| logic-analyzer | `04-logic-analyzers.toml` (4) | `logic_analyzer` | detect OK |
| usb-serial | `05-usb-serial.toml` (7: FT232R, FT2232H, CP2102/4, CH340, CH341 UART, PL2303HX, PL2303TA) | **none** | hal.py has no `usb_serial`/generic-TTY plugin — these only surface via `debug_probe`'s FTDI FT2232H entry (`hal.py:201`), the other 6 chips are invisible to `hal.py detect` even once the udev node exists |
| hdmi-capture | `06-hdmi-capture.toml` (3) | `hdmi_capture` | detect OK |
| usb-webcam | `07-usb-webcams.toml` (1: generic UVC) | **none** | no `usb_webcam`/UVC plugin in `REGISTRY` |
| ai-accelerator | `08-ai-accelerators.toml` (1: Coral TPU) | `coral_tpu` | detect OK |
| sdr | `09-sdr.toml` (4) | `sdr_receiver` | detect OK |
| network-adapter (incl. rtl8812bu) | `10-network-adapters.toml` (4: ASIX AX88179, RTL8153, **RTL8812BU**, MT7921U) | **none** | expected — these are in-kernel-driver devices with empty `udev_rule` by design (`limpet/hardware.d/build_iso_drivers.py:98-100`), but that also means `hal.py` cannot confirm them beyond `ip link`/`lsusb`; the ticket's own ask ("confirm ... Limpet HAL notification") has nothing to check against for this category |
| voltage-glitching | `11-voltage-glitching.toml` (2: ChipWhisperer, GlitchKit) | **none** | no plugin |
| fpga-programmer | `12-fpga-programmers.toml` (2: Lattice iCE40 boards, Xilinx Platform Cable USB II) | **none** | no plugin |

5 of 12 categories (usb-serial, usb-webcam, network-adapter, voltage-glitching,
fpga-programmer) have no `hal.py` plugin at all. For these, "confirm udev node + driver
bind" is still testable (via `lsusb`/`dmesg`/`ls /dev`), but "confirm Limpet HAL
notification" cannot pass on *any* hardware in that category today — not a per-device
bug, a missing plugin. Scope note: writing the missing plugins is not this ticket's task
(wq#3950 is the plug-test, not the detector); flagging it here so whoever runs the bench
test doesn't spend time chasing a HAL result that cannot exist yet for those 5 categories.

## The already-known gap: HAL notification is not wired at all

Independent of plugin coverage, there is no notify path for *any* category yet:
`ops/wq3809-limpet-hal/hal.py` only implements `detect`/`list-plugins` (one-shot CLI, line
26-29 of its own docstring) — no daemon, no `/run/limpet/hal.sock` listener, no
`limpet-hal-notify` binary. `docs/limpet-naming.md:28,42` confirms this is a known,
already-tracked gap ("the udev relay helper in hal-spec.md §2" — "there is no
`/run/limpet/hal.sock` listener"), not something discovered fresh this attempt. Every
category's "HAL notification" check will report SKIP, never PASS, until that daemon ships.

## What physically remains (Frank, or any operator with hands on Farida)

1. Pull current `main` onto Farida, regenerate the bundle there:
   `python3 limpet/hardware.d/build_iso_drivers.py --write-bundle /tmp/limpet-bundle`.
2. Install `90-limpet-hardware.rules` to `/etc/udev/rules.d/`, the modprobe conf to
   `/etc/modprobe.d/`, run `udevadm control --reload-rules`.
3. `dkms autoinstall` for whichever categories are DKMS-backed (per `prebake-plan.md`'s
   DKMS section) — build against Farida's *running* kernel, not the ISO's pinned
   `7.0.0-14-generic` (`limpet/iso-builder/driver_hook/kernel.pin`); those are two
   independent kernel targets (ISO offline pre-bake vs. live reference-hardware test) and
   the mismatch attempt 1 flagged is not itself a blocker.
4. For each of the 12 categories, plug in one device, then check (per category, using the
   matrix above to know what's actually checkable): `lsusb` shows the VID:PID, the udev
   node exists with correct permissions, `dmesg` shows the driver bound, and — only for
   the 7 categories with a plugin — `python3 ops/wq3809-limpet-hal/hal.py detect` reports
   the device. HAL notification will SKIP everywhere per the gap above; that is expected,
   not a failure to chase.
5. Record pass/fail per category back onto wq#3950 (or whatever ticket re-dispatches this
   physical step) with the actual `lsusb`/`dmesg`/`hal.py detect` output.

## Why this attempt stops here

Both SSH to Farida and inserting hardware are physical/network-reach acts outside this
sandbox (Tier-0 instinct 3's carve-out: the only legitimate Frank dependency is a
physical act nobody else can perform). Everything reachable without them has now been
re-verified twice (attempt 1 and this attempt) and extended with the plugin-coverage
finding above.

## Attempt 3 re-verification (2026-10-17)

Re-ran both suites against current `main` after attempt 2: still
`11 passed` (`limpet/hardware.d/test_build_iso_drivers.py`) and `17 passed`
(`ops/wq3809-limpet-hal/tests/test_hal.py`). SSH to Farida is still absent from
`/home/lumen/foreman/harness/worker-settings.json`'s allowlist, and wq#4120 (the queued
physical-act follow-up) is still `frank_gated`/unclaimed — nothing about the physical
blocker has changed since attempt 2.

One catalog fact *did* change and corrects step 3 above: wq#4093 (merged after attempt 2,
commit `82a6fcbd`) found the RTL8812BU `dkms_source` URL dead and verified the in-kernel
`rtw88_8822bu` module already claims `usb:v0BDApB812` on both the ISO's pinned kernel and
Farida's running-kernel ABI (`limpet/hardware.d/10-network-adapters.toml:43-48`). So the
network-adapter category is **not** DKMS-backed after all — all four devices (ASIX
AX88179, RTL8153, RTL8812BU, MT7921U) are in-kernel; RTL8812BU and MT7921U only need
their firmware blobs present via `linux-firmware`, no `dkms autoinstall` target. Step 3's
"whichever categories are DKMS-backed" still applies to the *other* 11 categories per
`prebake-plan.md`, just not to network-adapter. The HAL plugin-coverage gap (5/12
categories, `ops/wq3809-limpet-hal/hal.py:171-269`) and the missing notify daemon
(`docs/limpet-naming.md:28,42`) are both unchanged this attempt.

wq#3950 stays blocked rather than false-completed: the ticket's deliverable is the
physical confirmation itself, which still has not happened. The remaining work is
entirely the physical act tracked at wq#4120.

## Attempt 4 re-verification (2026-10-17)

Re-ran both suites against current `main` (28 commits landed since attempt 3's
`cde8f9cb`): still `11 passed` (`limpet/hardware.d/test_build_iso_drivers.py`) and
`17 passed` (`ops/wq3809-limpet-hal/tests/test_hal.py`). SSH to Farida is still absent
from the worker allowlist and wq#4120 is still `frank_gated`/unclaimed — the physical
blocker itself is unchanged.

One catalog fact did change in that window: wq#4155 (commit `9be5caec`) replaced the
multiprotocol category's "Bus Pirate 6" placeholder (previously `usb_ids = []`,
`confidence = "unverified"`, empty `udev_rule`) with a confirmed VID:PID and a real
rule (`limpet/hardware.d/03-multiprotocol-tools.toml:43,51-54`). No other commit in
that window touched `limpet/hardware.d/*.toml` or `ops/wq3809-limpet-hal/hal.py`'s
`REGISTRY` (`wq#4154` hackrf-mcp and `wq#4143` openocd-mcp/jtagprobe both added
plugin-local files only) — the plugin-coverage matrix above and the missing-notify-daemon
gap are both still accurate as written.

That catalog change surfaced a second, smaller finding: `limpet-plugin-buspirate-mcp`
ships its own udev rule (`plugins/limpet-plugin-buspirate-mcp/assets/90-limpet-buspirate-mcp.rules`,
installed to `/etc/udev/rules.d/90-limpet-buspirate-mcp.rules` by its `install.sh:49`)
for the same `1209:7331` device the catalog now also emits into the consolidated
`90-limpet-hardware.rules`. The two rule bodies aren't identical: the plugin's copy adds
`TAG+="limpet-multiprotocol"` that the catalog-generated rule lacks. Both files can
coexist in `/etc/udev/rules.d/` (different filenames, same `MODE`/`GROUP`, so permissions
don't conflict), but anything gated on the `limpet-multiprotocol` tag only fires if the
plugin's own rules file is also installed — physically installing only the ISO
bundle's `90-limpet-hardware.rules` (step 2 above) is not equivalent to also running
the plugin's `install.sh`. Whoever runs the physical step for the multiprotocol category
should install both, not assume the consolidated bundle alone covers a plugin-shipped
device.
