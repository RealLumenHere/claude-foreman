# limpet-plugin-mcjtag

Secondary/alternate JTAG/SWD debug probe plugin for Limpet, wrapping
[mcjtag](https://mcjtag.warehack.ing) (PyPI v2026.2.18) by Ryan Malloy.

## Secondary to openocd-mcp -- do not enable both

mcjtag and [limpet-plugin-openocd-mcp](../limpet-plugin-openocd-mcp/) both talk
to OpenOCD's TCL server on `localhost:6666` by default. Running both plugins
enabled at once means two MCP servers racing for the same port: whichever
connects second either fails outright or hijacks the first one's session.

This plugin ships disabled (`[mcp].enabled = false` in `limpet-plugin.toml`).
openocd-mcp remains the default primary because it self-manages the OpenOCD
process; mcjtag is the fallback for when openocd-mcp is unavailable, failing
health, or when an externally-managed OpenOCD instance is preferred (mcjtag
can attach to one already running via `OPENOCD_HOST`/`OPENOCD_PORT` instead of
spawning its own).

## What it does

Provides JTAG/SWD debug access to microcontrollers via OpenOCD: memory and
register read/write, flash programming, JTAG chain scanning, and SVD-based
peripheral register decoding. 17 tools across 9 categories; see
[Tools](#tools) below.

## Tested hardware

Per upstream docs, mcjtag is tested with a Treedix DAP-Link V1 (CMSIS-DAP)
against an STM32F103C8T6 (Blue Pill) target, over both SWD and JTAG. Because
mcjtag talks to OpenOCD rather than the probe directly, any probe OpenOCD
supports works in principle -- only the `OPENOCD_CONFIG` interface/target
selection changes.

| Probe | VID:PID | Interface |
|---|---|---|
| CMSIS-DAP (e.g. Treedix DAP-Link) | 0d28:0204 | SWD / JTAG |

## Resources

mcjtag's README advertises **7** read-only MCP resources (`jtag://` URI
scheme, pollable without tool calls). Reading the actual v2026.2.18 source
(`src/mcjtag/resources.py`) turned up **9** -- two (`jtag://breakpoints`,
`jtag://swd/dap`) exist in code but aren't listed in the upstream resource
table. Documented here for accuracy; all are read-only.

| URI | Description | In upstream docs? |
|-----|-------------|---|
| `jtag://target/state` | Execution state and program counter | yes |
| `jtag://registers/all` | All CPU register values | yes |
| `jtag://flash/banks` | Flash bank topology | yes |
| `jtag://jtag/chain` | JTAG scan chain with TAPs | yes |
| `jtag://svd/peripherals` | Loaded SVD peripheral list | yes |
| `jtag://svd/{peripheral}` | Decoded register values for a peripheral | yes |
| `jtag://transport/info` | Active transport and adapter info | yes |
| `jtag://breakpoints` | Active breakpoints and watchpoints | no (code only) |
| `jtag://swd/dap` | DAP identification and Access Port summary | no (code only) |

Every resource returns `{"error": "Not connected to OpenOCD. Use connect or
start_openocd first."}` when there is no active session, instead of raising.

## Tools

| Category | Tools | Purpose |
|----------|-------|---------|
| Connection | `connect`, `start_openocd`, `disconnect` | Session lifecycle |
| Diagnostics | `probe_diagnostics` | 9-point health check |
| Target | `target_state`, `target_control` | Halt, resume, step, reset |
| Memory | `read_memory`, `write_memory`, `search_memory` | Read, write (SRAM-safe by default), search |
| Registers | `read_registers`, `write_register` | CPU register access |
| Flash | `flash_info`, `flash_program` | Bank info, program firmware |
| JTAG | `jtag_scan`, `jtag_shift` | Scan chain, IR/DR shift |
| SVD | `svd_inspect` | Peripheral register decoding |
| Raw | `raw_command` | Direct OpenOCD TCL access |

## Safety-conservative OpenOCD configs

Upstream's `pyproject.toml` excludes `*.cfg` from the sdist, so the two
configs its README documents aren't actually shipped in the PyPI package.
This plugin mines them from upstream's docs
(`docs/src/content/docs/reference/openocd-configs.mdx`) and ships them in
[`assets/`](assets/) so `OPENOCD_CONFIG` auto-spawn mode works out of the box:

- `openocd-daplink-swd.cfg` -- CMSIS-DAP over SWD, `adapter speed 1000` (kHz,
  a conservative default), default target `stm32f1x` (Blue Pill).
- `openocd-daplink-jtag.cfg` -- same probe over JTAG (4-wire, for chain
  scanning), same conservative 1000 kHz default.

Both default to the STM32F1x target; edit the `source [find target/...]`
line to point at a different chip before use.

## Safety defaults (mcjtag's own, independent of Limpet's energize gate)

- `write_memory` only allows SRAM by default
  (`MCJTAG_SAFE_WRITE_RANGES=0x20000000-0x20100000`). Flash, peripheral, and
  system writes are blocked unless the range is widened or set to `none`.
- `raw_command` blocks destructive OpenOCD TCL commands (flash erase, memory
  writes, reset, shutdown) via a deny-list unless `MCJTAG_ALLOW_RAW=true`.
  Upstream documents this as "a best-effort safety measure, not a sandbox."
- `search_memory` is capped at `MCJTAG_MAX_SEARCH_RANGE` bytes (default 1 MiB)
  per call.
- `flash_program` validates file existence, extension, and size before
  writing.

## Energize gate (Limpet-side, in addition to the above)

- **Gated:** `target_control`, `write_memory`, `write_register`,
  `flash_program`, `jtag_shift`, `raw_command`
- **Ungated:** `connect`, `start_openocd`, `disconnect`, `probe_diagnostics`,
  `target_state`, `read_memory`, `search_memory`, `read_registers`,
  `flash_info`, `jtag_scan`, `svd_inspect`

## Connection modes

- **Auto-spawn** -- set `OPENOCD_CONFIG` to one of the shipped configs;
  mcjtag starts and stops OpenOCD itself.
- **Auto-connect** -- set `OPENOCD_HOST`/`OPENOCD_PORT` only; mcjtag attaches
  to an OpenOCD instance already running (e.g. managed as a system service).
- **Manual** -- set nothing; call `connect()` or `start_openocd()` explicitly.

## Installation

```bash
./install.sh
```

Requires: Python 3.11+, pip, OpenOCD (installed automatically if missing).

## License

MIT, per upstream PyPI metadata (classifier, `pyproject.toml`'s `license =
"MIT"` SPDX field, and PKG-INFO's `License-Expression: MIT` all agree). No
LICENSE file ships in the sdist and the upstream repo URL
(`git.supported.systems/warehack.ing/mcjtag`) 404s, so the literal license
text could not be cross-checked against the SPDX declaration -- treat as
**metadata-confirmed, not vetted**, until that gap is closed.
