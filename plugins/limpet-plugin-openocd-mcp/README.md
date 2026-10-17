# limpet-plugin-openocd-mcp

Primary JTAG/SWD debug probe plugin for Limpet, wrapping
[microhenrio/openocd-mcp](https://github.com/microhenrio/openocd-mcp) (MIT, PyPI v0.4.0).

## What it does

Provides JTAG/SWD debug access to microcontrollers and SoCs via OpenOCD. Unlike
mcjtag (which requires OpenOCD to already be running), openocd-mcp manages the
OpenOCD process lifecycle itself — plug in a probe and go.

## Supported probes

| Probe | VID:PID | Interface |
|---|---|---|
| ST-Link/V2 | 0483:3748 | SWD |
| ST-Link/V2-1 | 0483:374b | SWD |
| SEGGER J-Link | 1366:0101 | JTAG |
| Black Magic Probe | 1d50:6018 | SWD |
| FTDI FT2232H (Tigard) | 0403:6010 | JTAG |
| CMSIS-DAP | 0d28:0204 | SWD |

## Energize gate

Write/destructive operations require energize-confirm:
- **Gated:** flash_write, flash_erase, halt, reset, step, register_write, memory_write
- **Ungated:** flash_read, jtag_scan, identify_chip, register_read, memory_read

## Installation

```bash
./install.sh
```

Requires: Python 3.10+, pip, OpenOCD (installed automatically if missing).

## License

MIT (upstream: microhenrio/openocd-mcp)
