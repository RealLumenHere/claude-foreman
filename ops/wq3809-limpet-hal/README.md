# wq#3809 — LIMPET hardware abstraction layer

`hal.py` auto-detects RE/lab hardware on a node and exposes it to an agent as one JSON
document: hardware class, capability list, device node, connected/not_connected. It is
signature-based (known USB VID:PID pairs + descriptor substrings, checked against the
live `lsusb` device list), not probing — a match means "this kind of hardware is
present," not that it has been opened or exercised.

```
python3 hal.py detect         # only hardware currently plugged in
python3 hal.py list-plugins   # every supported hardware class, connected or not
```

`detect` is what an agent should read before reaching for a hardware-specific tool
(openocd, sigrok-cli, minipro, rtl_sdr, adb, ...): it tells the agent whether the
hardware exists on this node at all, and which device node to point the tool at, instead
of the agent guessing and getting a confusing tool-level error.

## Registered hardware classes (`hal.REGISTRY`, 8 entries)

| hw_type | capabilities | device node |
|---|---|---|
| `coral_tpu` | image_classification, on_device_ml, edgetpu_inference | USB bus path |
| `hdmi_capture` | visual_input, screen_reading, kvm | `/dev/videoN` (V4L2) |
| `debug_probe` | jtag, swd, openocd_target, gdb_server | `/dev/ttyACM*` where exposed, else USB bus path |
| `logic_analyzer` | logic_capture, protocol_decode, pulseview | USB bus path (sigrok talks libusb directly) |
| `flash_programmer` | spi_flash_read, spi_flash_write, eeprom_program, minipro | USB bus path |
| `bus_interface` | i2c, spi, uart, generic_bus_bridge | `/dev/ttyUSB*` where exposed, else USB bus path |
| `target_device` | adb, edl_flash, maskrom_flash | USB bus path |
| `sdr_receiver` | rf_capture, iq_sample, rtl_sdr | USB bus path |

Each class is a `HardwarePlugin` (`hal.py:HardwarePlugin`) carrying its own VID:PID set
and, where the kernel gives the device a stable node (V4L2 capture, CDC-ACM serial), a
resolver that walks `/sys/class/*/.../device` back to the matching `busnum`/`devnum` to
find it (`hal.py:_node_for_sysfs_class`). Adding a ninth class means adding one more
`HardwarePlugin(...)` entry to `REGISTRY` — no other code changes.

## Matching rule (wq#3819)

`usb_ids` is the primary signal. `desc_patterns` is a fallback for rebranded clones that
ship an unlisted PID but keep the product string, so every pattern must name a **product
or USB mode** — `rtl2838`, `qdl mode`, `mask rom mode`, `black magic probe` — never a bare
vendor name. wq#3814 reproduced three plugins matching the vendor string alone, which made
an ordinary Realtek NIC report as an SDR receiver (`rf_capture`/`rtl_sdr`), a Qualcomm WWAN
modem as an EDL-flashable target (`edl_flash`/`maskrom_flash`), and a Cypress mouse as a
logic analyzer. `tests/test_hal.py:ORDINARY_LSUSB_SAMPLE` is the regression guard, and
`install.sh --verify` reruns the same probe against the deployed copy via
`verify_no_false_positive.py`.

## SoC boot-mode VID:PID map (wq#3923, shipped as wq#3940's target device database)

`target_device`'s own VID:PID set (60 pairs, wq#4188) is what decides whether a device
is a target at all. Everything below only *enriches* a device that set already matched.
The set is curated from the source-verified catalog
`/home/lumen/projects/limpet/data/usb-device-ids.toml` (generated 2026-10-15, every row
cited to a tool's udev rules/source or usb.ids). It holds only the flashing modes
(MaskROM, BROM/preloader, EDL/Sahara, fastboot, FEL, Odin download, DFU, ROM
recovery/USB boot) at confidence >= 0.8, and drops any pair that usb.ids also gives to a
normal-mode phone. The catalogs described below are **not** safe to gate on: they carry
vendor-only wildcards (any ASUS/LG/Sony/ZTE device), normal-mode reference rows (MTP, ADB,
USB-serial bridges, WWAN diag) and recalled-not-sourced mislabels (ISSUE-LOG #1365;
`04e8:6860` is a Samsung phone in MTP mode, catalogued as Odin download).
`tests/test_hal.py:CATALOGUED_NORMAL_MODE_LSUSB_SAMPLE` fails if the gate is widened that
way. The `2207` "any PID with a non-zero high byte" Rockchip fallback in that catalog is
not used, so a Rockchip SoC newer than RK3588 is caught only by the `mask rom mode`
descriptor pattern.

`usb_boot_signatures.toml` is wq#3923's original
(100+ entry) research artifact: MediaTek BROM, Qualcomm EDL/Sahara, Rockchip MaskROM/Loader,
Amlogic burn mode, Allwinner FEL, Samsung Odin, Broadcom/RPi USB boot, TI/NXP/STM32/
industrial-IoT DFU and ROM-boot modes, Xiaomi/Spreadtrum, Apple DFU, Realtek, HiSilicon,
plus the vendor-independent ADB/fastboot/DFU/CDC-ACM USB class signatures and a set of
Android-OEM VIDs. See `research/WQ3923_USB_BOOT_MODE_SIGNATURES.md` for the sourcing note
and confidence methodology.

wq#3940 reshaped that same research into the on-disk, per-vendor-family database this HAL
plugin and the Target Device Wizard's IDENTIFY stage (`docs/target-wizard-spec.md:144-156`,
the `usb-vid-pid` evidence source) actually query: `limpet/target-devices.d/*.toml` (one
file per vendor family — `mediatek.toml`, `qualcomm.toml`, `rockchip.toml`, `amlogic.toml`,
`allwinner.toml`, `samsung.toml`, `broadcom.toml`, `ti-nxp.toml`, `apple.toml`,
`realtek.toml`, `hisilicon.toml`, `generic-usb.toml`), loaded via `limpet/target_devices.py`
(`lookup(vid, pid, class_code=...)`). `detect()` above calls this for every `target_device`
match and attaches the result as `target_db_matches` — real-time enrichment, not a second
hand-rolled table. The matching udev rules ship at
`limpet/target-devices.d/91-limpet-targets.rules`, generated (never hand-edited) by
`python3 limpet/target_devices.py render-udev-rules`. See
`design/wq3940_target_device_database.md` for the schema and file layout.

```
python3 ../../limpet/target_devices.py lookup 05c6 9008   # -> EDL, edl / qdl, confidence high
python3 usb_boot_signatures.py lookup 05c6 9008            # original wq#3923 artifact, confidence 0.95
```

## Known gaps

- PCIe/M.2 Coral TPUs (`/dev/apex_*`) are not detected: they never appear in `lsusb`, and
  there is no way to correlate an apex node with a USB bus/device pair, so the old
  `_apex_node_for` resolver could only ever report a PCIe card's node for a USB
  Accelerator. Dropped in wq#3819; covering PCIe Corals needs an `lspci`/sysfs scan this
  detector does not have.
- A target device in normal ADB mode is not detected either. Only the bootrom/flashing
  modes are detected: the listed VID:PIDs plus the `qdl mode`, `mask rom mode` and
  `in flashing mode` descriptor patterns. The
  `adb` capability on `target_device` therefore means "this device is flashable and speaks
  adb when booted", not "adb is live right now"; ask `adb devices` for that.

## Not verified

The VID:PID table is sourced from each tool's own udev rules/docs (openocd, sigrok,
flashrom, Black Magic Probe, RTL-SDR), not from a physical unit plugged into this
worktree's sandbox — this sandbox has no USB bus to test against. Before relying on a
specific entry, run `python3 hal.py detect` on the node with that hardware actually
connected and confirm the row it reports, per the repo's verify-before-trust doctrine.
`tests/test_hal.py` covers the parsing/matching/resolution logic against canned
`lsusb`/sysfs fixtures, not against real hardware.
