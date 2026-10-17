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
- **Ungated:** flash_read, jtag_scan, identify_chip, register_read, memory_read, classify_access

## classify_access (wq#4143)

Pre-energize OPEN/LOCKED/DEAD classifier, served by `mcp/classify_access.py` rather than
the wrapped upstream binary (a sealed third-party package we can't add tools to). It
attaches read-only to the OpenOCD Tcl RPC port openocd-mcp already manages, scans the
chain, and does one transient halt + memory peek to tell an operator whether a target's
debug access is:

- **OPEN** — DP/TAP responds, core halts, memory reads look like a real vector table.
- **LOCKED** — DP/TAP responds but halt/reads fail or return the readout-protection
  sentinel (RDP/CRP/APPROTECT). A vendor-specific unlock hint is included when known.
- **DEAD** — no DP/TAP response at all.

Ported from [BrownFineSecurity/iothackbot](https://github.com/BrownFineSecurity/iothackbot)'s
`tools/iothackbot/core/jtagprobe_core.py` (MIT License); upstream drives SEGGER J-Link,
this port drives OpenOCD's Tcl RPC instead. See `mcp/classify_access.py` for the full
provenance note and `tests/test_classify_access.py` for coverage of all three verdicts.

## Installation

```bash
./install.sh
```

Requires: Python 3.10+, pip, OpenOCD (installed automatically if missing).

## License

MIT (upstream: microhenrio/openocd-mcp)
