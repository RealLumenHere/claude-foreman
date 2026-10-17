# limpet-plugin-jlink-mcp

Multi-backend embedded debug probe plugin for Limpet, wrapping
[Klievan/jlink-mcp](https://github.com/Klievan/jlink-mcp) (MIT, npm v0.7.0,
47 tools).

**Corrected provenance (wq#4140):** this was previously catalogued under
`vhdirk/jlink-mcp`. That is wrong — the real upstream, confirmed against the
npm registry (`maintainers: klievan`, `repository: Klievan/jlink-mcp`) and
GitHub, is **Klievan/jlink-mcp**. No reference to `vhdirk/jlink-mcp` was found
anywhere else in this repo as of this ticket, so there was no other catalog
entry to fix.

## What it does

Connects an MCP client to an ARM Cortex-M target over one of three debug
probe backends, selected by a single `PROBE_TYPE` env var:

| `PROBE_TYPE` | Probe hardware | Upstream status | RTT support |
|---|---|---|---|
| `jlink` (default) | SEGGER J-Link, J-Link OB, J-Link EDU | Production | Yes |
| `openocd` | ST-Link, CMSIS-DAP, FTDI | Beta | No |
| `blackmagic` | Black Magic Probe (serial GDB server) | Beta | No |

Native `jlink` mode adds RTT log streaming (with level/module/regex search),
automatic ARM Cortex-M fault decoding (`diagnose_crash`), CMSIS-SVD peripheral
register decode, and a Trice/Pigweed telnet proxy — none of which are
available through the `openocd`/`blackmagic` backends. Hardware-CI-tested by
upstream on a real nRF52840-DK (`test/hil/`, `npm run test:hil`).

## Supported probes (one hardware.d entry per backend)

`PROBE_TYPE` is a single env-var switch across three backends, so this plugin
ships three hardware manifest entries (`hardware/multi-backend-probe.toml`)
rather than one:

| Probe | VID:PID | `PROBE_TYPE` |
|---|---|---|
| SEGGER J-Link | 1366:0101 | `jlink` |
| Black Magic Probe | 1d50:6018 | `blackmagic` |
| ST-Link/V2 (generic OpenOCD adapter) | 0483:3748 | `openocd` |

The `openocd` backend also works with CMSIS-DAP and FTDI adapters (OpenOCD's
own device support); the ST-Link entry here is the representative
generic-adapter VID:PID, matching `limpet/hardware.d/02-debug-probes.toml`.

## Energize gate

This plugin's tool set has no upstream gate annotations, so the
flash/write/execution-control split below is this packaging's own judgment
call, made by direct analogy to `limpet-plugin-openocd-mcp`:

- **Gated:** `flash`, `erase`, `write_memory`, `halt`, `resume`, `reset`,
  `step`, `set_breakpoint`, `clear_breakpoints`, `rtt_send`, `gdb_command`,
  `probe_command`, `gdb_server_start`, `gdb_server_stop`
- **Ungated:** everything else (device/peripheral/register reads, RTT reads,
  GDB connect/backtrace/wait/disconnect, telnet proxy status/read, config/info
  queries)

## Installation

```bash
./install.sh
```

Requires: Node.js 18+, npm. The `jlink` backend additionally needs the
[SEGGER J-Link software](https://www.segger.com/downloads/jlink/) (not
installed by this script, closed-source); `openocd` needs `openocd`;
`blackmagic` needs `arm-none-eabi-gdb`.

## License

MIT (upstream: Klievan/jlink-mcp). Verified by fetching
`https://raw.githubusercontent.com/Klievan/jlink-mcp/main/LICENSE` directly
(2026-10-17): standard MIT text, copyright "The Sprk Factory", matching the
repo's license badge. No longer "unconfirmed" as of this ticket.
