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
| `coral_tpu` | image_classification, on_device_ml, edgetpu_inference | `/dev/apex_*` (PCIe/M.2) else USB bus path |
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

## Not verified

The VID:PID table is sourced from each tool's own udev rules/docs (openocd, sigrok,
flashrom, Black Magic Probe, RTL-SDR), not from a physical unit plugged into this
worktree's sandbox — this sandbox has no USB bus to test against. Before relying on a
specific entry, run `python3 hal.py detect` on the node with that hardware actually
connected and confirm the row it reports, per the repo's verify-before-trust doctrine.
`tests/test_hal.py` covers the parsing/matching/resolution logic against canned
`lsusb`/sysfs fixtures, not against real hardware.
