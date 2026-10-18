# wq#3940 — LIMPET HAL target device database (SoC boot-mode VID:PID TOML files)

> **wq#4190 update:** the entry counts and "none dropped" claim below describe this
> ticket's original 1:1 reshape of wq#3923's research. wq#4190 later found that
> research recalled-not-sourced (ISSUE-LOG #1365) and regenerated the
> vendor-covered rows (Rockchip, MediaTek+OEM, Qualcomm+OEM, Allwinner, Amlogic,
> Samsung, Broadcom, Apple, and the NXP/STM32 slice of ti-nxp.toml) from the
> source-verified catalog `/home/lumen/projects/limpet/data/usb-device-ids.toml`,
> retiring `usb_boot_signatures.toml`/`.py` as superseded. The schema, loader,
> and udev-rule generation described here are unchanged; only the catalog data
> and entry counts are stale. See `ops/wq3809-limpet-hal/README.md`.

Reshapes wq#3923's SoC boot-mode USB VID:PID research
(`ops/wq3809-limpet-hal/usb_boot_signatures.toml`,
`research/WQ3923_USB_BOOT_MODE_SIGNATURES.md`) into the on-disk, per-vendor-family
database this ticket asked for, wires real-time HAL detection against it, and generates
the matching udev rules. Blocked by and reuses wq#3923's research wholesale — this
ticket is a build/reshape, not new RE research, so no fresh fleet-search pass was run.

## 1. Where the catalog lives

`limpet/target-devices.d/*.toml` — 12 files, one per vendor family named in the ticket,
102 entries total (every entry from the wq#3923 source, none dropped). Mirrors the
`limpet/hardware.d/*.toml` convention from wq#3924
(`design/wq3924_hardware_driver_catalog.md`): a drop-in catalog for
`/etc/limpet/target-devices.d/`, distinct from a `limpet-plugin.toml` — this is reference
data about every known boot-mode signature, not one active HAL binding.

Vendor -> file mapping (by the source TOML's own `vendor` field, not guessed): MediaTek
Inc. -> `mediatek.toml`; Qualcomm, Inc. -> `qualcomm.toml`; Fuzhou Rockchip Electronics ->
`rockchip.toml`; Amlogic, Inc. -> `amlogic.toml`; Allwinner Technology -> `allwinner.toml`;
Samsung Electronics -> `samsung.toml`; Broadcom Corp -> `broadcom.toml`;
STMicroelectronics/TI/NXP/Atmel-Microchip/Nordic/Espressif/Cypress (the industrial-IoT MCU
bucket the wq#3923 source itself grouped as one section) -> `ti-nxp.toml`; Apple, Inc. ->
`apple.toml`; Realtek Semiconductor Corp. -> `realtek.toml`; HiSilicon-Balong (Huawei) ->
`hisilicon.toml`. Everything without its own named file in the ticket — the
vendor-independent USB-class signatures (DFU, ADB, fastboot, CDC-ACM, mass storage,
RNDIS), Google's ADB/fastboot VIDs, the Android-OEM wildcard VIDs (Sony, Motorola, HTC,
ZTE, LG, OnePlus, OPPO, ASUS, Vivo, Meizu), Xiaomi's composite VID, Spreadtrum/Unisoc, and
the generic USB-UART bridge chips (FTDI/Silicon Labs/WCH/Prolific) — lands in
`generic-usb.toml` (34 entries), matching that file's ticket description: "ADB, fastboot,
DFU class."

Per-entry fields (direct mapping to the ticket's requested output, plus `match_type` and
`class_code` which the usb_class rows need to be meaningful):
`match_type` (`vid_pid` | `vid_pid_wildcard` | `usb_class`), `usb_vid`, `usb_pid`,
`class_code`, `vendor`, `soc_family`, `soc_model`, `mode_name`, `recommended_tool`,
`confidence` (`high`/`medium`/`low` — same three-tier scale as `limpet/hardware.d`, not
wq#3923's raw 0.0-1.0 float: `>=0.75` high, `0.45-0.74` medium, `<0.45` low), `notes`.

`soc_family`/`soc_model` is a reshape of wq#3923's single `soc_family` field, split on
the first top-level comma or colon (nesting-aware, so "early MSM/Snapdragon (pre-Firehose,
Sahara-only)" doesn't split inside the parenthetical): the part before the split is
`soc_family` (a short platform tag), the whole original text is `soc_model` (so no detail
is lost). Where the source text has no natural split point, both fields hold the same
text — correct for entries like Allwinner FEL where one PID genuinely spans the entire
vendor's SoC lineup. Several entries' `notes` explicitly say the boot-mode PID does not
discriminate chip model at all (MediaTek, Allwinner, Amlogic) — `soc_model` there is a
*covered-models* list, not a resolved identification; the flashing tool's own post-connect
handshake is what actually names the chip.

## 2. Loader, lookup, and udev rule generation

`limpet/target_devices.py` — same shape as the wq#3923 artifact's own
`usb_boot_signatures.py` and wq#3924's `build_iso_drivers.py`: `load_catalog()` walks every
`*.toml` in the directory (pure, no hand-maintained duplicate list), `lookup(vid, pid,
class_code=...)` resolves a device against it (three match types, highest confidence
first), `render_udev_rules(devices)` derives the udev rules text straight from the
catalog. Tests: `limpet/test_target_devices.py`, 10/10 passing, including a probe that
must be able to fail (wq#2630: an unlisted VID:PID returns `[]`) and a drift guard
(`test_checked_in_rules_file_matches_current_catalog`) that fails if the committed rules
file and the catalog disagree.

`limpet/target-devices.d/91-limpet-targets.rules` is generated — never hand-edited — by
`python3 limpet/target_devices.py render-udev-rules`. One rule per `vid_pid`/
`vid_pid_wildcard` entry (`SUBSYSTEM=="usb", ATTR{idVendor}=="...", ATTR{idProduct}=="..."`,
the wildcard variant omitting the `idProduct` clause) and one interface-level rule per
`usb_class` entry (`ENV{DEVTYPE}=="usb_interface", ATTRS{bInterfaceClass}=="cc",
ATTRS{bInterfaceSubClass}=="ss", ATTRS{bInterfaceProtocol}=="pp"` — class attributes live
on the interface, not the device, so these need the `ATTRS` attribute-walk form rather
than `ATTR`). Every rule tags the device `limpet_target` and sets
`ENV{LIMPET_TARGET_MODE}` to a slugged `mode_name`, which is the hook a HAL daemon run
under `udevadm monitor`/systemd-udevd would key off for a real-time plug notification —
102 rules, one per catalog entry (verified `len(rules) == len(devices)` in the test suite).

## 3. HAL integration

`ops/wq3809-limpet-hal/hal.py`'s `target_device` plugin already matches the 3 highest-
confidence pairs directly (Qualcomm EDL, Rockchip MaskROM/Loader, Allwinner FEL) plus a
`desc_patterns` fallback for rebrands/unlisted PIDs in the same modes (wq#3819). `detect()`
now calls `limpet/target_devices.py`'s `lookup()` for every `target_device` match and
attaches the result as `target_db_matches` — vendor/soc_family/soc_model/mode_name/
recommended_tool/confidence, highest-confidence first, `[]` when the specific VID:PID
isn't in the catalog (the plugin can still match via `desc_patterns` on a pair the catalog
has never seen; enrichment degrades gracefully rather than raising). The import is
wrapped in `try/except ImportError` so `hal.py` keeps working, just without enrichment, if
`limpet/` isn't alongside this `ops/` tree. Matching/`REGISTRY`/`desc_patterns` logic is
untouched — this is additive enrichment only, so it carries no risk to the wq#3819/wq#3814
false-positive regression guard (`tests/test_hal.py::ORDINARY_LSUSB_SAMPLE`). New tests:
`test_target_device_is_enriched_from_wq3940_catalog`,
`test_target_device_enrichment_empty_for_pair_outside_catalog` (the latter is the
must-be-able-to-fail probe for this change specifically: a `dead:beef` pair that matches
`target_device` only via the `desc_patterns` fallback, outside the catalog's own matched
population). Full suite: `ops/wq3809-limpet-hal/tests/` 25/25 passing.

## 4. ISO scope boundary

Same boundary wq#3924 drew: packaging `limpet/target-devices.d/*.toml` and
`91-limpet-targets.rules` into the actual bootable ISO image (copying into
`/etc/limpet/target-devices.d/` and `/etc/udev/rules.d/` at image-build time) is the
ISO-build ticket's job, not this one's — this ticket ships the repo-resident source of
truth those paths are built from, consistent with how `limpet/hardware.d/` ships without
its own install.sh. No live filesystem write outside this worktree was made or attempted.
