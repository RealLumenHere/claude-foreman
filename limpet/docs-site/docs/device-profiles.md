# Device Profiles

A **device profile** teaches the Target Device Wizard to recognize a device from its
USB identifiers and to recommend the right flashing or recovery tool. Profiles live in
`limpet/target-devices.d/*.toml`, grouped by silicon vendor
(`broadcom.toml`, `qualcomm.toml`, `mediatek.toml`, `rockchip.toml`, …). Each is a
drop-in catalog loaded by `limpet/target_devices.py`, which enriches the HAL's
real-time view and generates the udev rules.

## The schema

Each profile file is a list of `[[device]]` entries. The fields:

| Field | Meaning |
|---|---|
| `match_type` | `vid_pid`, `vid_pid_wildcard` (`pid="*"`), or `usb_class` |
| `usb_vid`, `usb_pid` | lowercase hex strings; empty when `match_type = "usb_class"` |
| `class_code` | `cc:ss:pp` hex triplet; empty unless `match_type = "usb_class"` |
| `vendor` | silicon / OEM vendor name |
| `soc_family` | short chip-family or platform tag |
| `soc_model` | specific chip model(s) this pair is known to cover |
| `mode_name` | human name of the boot / recovery mode |
| `recommended_tool` | the tool to dispatch to |
| `confidence` | `high` (≥0.75), `medium` (0.45–0.74), or `low` (<0.45) |
| `notes` | caveats, protocol notes, and **the source** for the entry |

## A worked example

From `limpet/target-devices.d/broadcom.toml` — a Raspberry Pi 5 in BootROM USB mode:

```toml
[[device]]
match_type = "vid_pid"
usb_vid = "0a5c"
usb_pid = "2712"
class_code = ""
vendor = "Broadcom Corp"
soc_family = "BCM2712"
soc_model = "Raspberry Pi 5/CM5"
mode_name = "BootROM USB (bootcode5.bin path)"
recommended_tool = "rpiboot"
confidence = "high"
notes = "Source: raspberrypi/usbboot main.c."
```

When a device enumerating as `0a5c:2712` is plugged in, the wizard identifies it as a
Pi 5 in BootROM USB mode and recommends `rpiboot`, at high confidence because the
pairing is sourced from `rpiboot`'s own source.

## Submitting a new profile

!!! danger "Every entry must cite a source — recalled is not sourced"
    The one rule that matters: a device entry is only as trustworthy as its source.
    A VID:PID you *remember* is not evidence; a VID:PID from the device's own flashing
    tool, a vendor datasheet, or a verified USB-ID catalog is. Put that source in
    `notes`. Entries added from memory have been pulled back out of this database more
    than once — do not add one.

To add or correct a profile:

1. **Find the right file.** Group by silicon vendor (add a new `vendor.toml` only for
   a vendor that has none).
2. **Capture the real identifiers.** Put the device in its boot/recovery mode and read
   the VID:PID (`lsusb`), the enumerated mode, and the class code if you are matching
   by class rather than by exact PID.
3. **Set `confidence` honestly.** `high` only when the PID→chip mapping is stated by a
   primary source. If several models share a boot PID (common — vendors often do not
   vary the PID by model), say so in `notes` and let the flashing tool's own
   post-connect handshake resolve the exact model. A `medium`/`low` entry must not gate
   an irreversible action without an operator confirm.
4. **Write the source into `notes`.** Name the datasheet, the tool source file, or the
   catalog. No source, no entry.
5. **Validate and render.** Confirm the file parses and the udev rules regenerate:

    ```sh
    python3 limpet/target_devices.py render-udev-rules
    ```

6. **Open a pull request** with the new entry and its source.

Because identification feeds the confidence gate in the
[Target Device Wizard](user-guide/target-wizard.md), a careful profile directly makes
the appliance safer: a correct, high-confidence match lets the wizard proceed, and an
honest low-confidence one makes it stop and ask.
