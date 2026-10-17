# limpet-plugin-embedded-debugger-mcp

Dual-backend ARM Cortex-M / RISC-V / Xtensa (ESP32) debug probe plugin for Limpet,
wrapping [Adancurusul/embedded-debugger-mcp](https://github.com/Adancurusul/embedded-debugger-mcp)
(MIT, Rust, tag v0.3.0). Top pick of the wq#4096 MCP server evaluation batch
(docs/mcp-server-evaluations.md Section 5).

## What it does

Exposes one MCP tool set (24 tools) over two interchangeable engines, selected at
`connect`:

- `probe-rs` (default) — native, full flash + RTT support.
- `openocd` (experimental upstream) — connects to an already-running `openocd`
  over its GDB port. Needed for targets probe-rs does not cover (e.g. Xtensa
  ESP32 via openocd-esp32); memory access and halt/run/step/reset are validated
  on real ESP32-S3, but flash and RTT are probe-rs only, and register reads use
  ARM gdb register numbers (PC/SP are wrong on Xtensa).

Tool groups: probe discovery (`list_probes`, `connect`, `probe_info`,
`disconnect`), core control (`halt`, `run`, `reset`, `step`, `get_status`),
memory/breakpoints (`read_memory`, `write_memory`, `set_breakpoint`,
`clear_breakpoint`), flash (`flash_erase`, `flash_program`, `flash_verify`,
`run_firmware`), RTT (`rtt_attach`, `rtt_detach`, `rtt_channels`, `rtt_read`,
`rtt_write`), and AI-facing crash diagnosis (`diagnose_fault`,
`unwind_exception`).

## Supported probes

Same probe-rs/OpenOCD compatible device set as `openocd-mcp`:

| Probe | VID:PID | Interface |
|---|---|---|
| ST-Link/V2 | 0483:3748 | SWD |
| ST-Link/V2-1 | 0483:374b | SWD |
| SEGGER J-Link | 1366:0101 | JTAG |
| Black Magic Probe | 1d50:6018 | SWD |
| FTDI FT2232H (Tigard) | 0403:6010 | JTAG |
| CMSIS-DAP | 0d28:0204 | SWD |

## Energize gate

Per-tool energize gate (wq#4139), scoped to flash/halt/write-class tools:

- **Gated:** `halt`, `run`, `reset`, `step`, `write_memory`, `flash_erase`,
  `flash_program`, `run_firmware`, `rtt_write`
- **Ungated (read-only / session management):** `list_probes`, `connect`,
  `probe_info`, `disconnect`, `get_status`, `read_memory`, `set_breakpoint`,
  `clear_breakpoint`, `flash_verify`, `rtt_attach`, `rtt_detach`,
  `rtt_channels`, `rtt_read`
- **Explicitly exempted even though crash-diagnosis-adjacent:**
  `diagnose_fault` and `unwind_exception` only read SCB fault registers / DWARF
  debug info — they report evidence, they assert nothing and change nothing on
  the target.

## Installation

```bash
./install.sh
```

Requires: Rust stable toolchain (`cargo`). Builds and pins upstream tag
`v0.3.0` via `cargo install --locked --git ... --tag v0.3.0`.

## Companion: Serial MCP Server (UART HAL class, not wrapped here)

Adancurusul also publishes
[serial-mcp-server](https://github.com/Adancurusul/serial-mcp-server) (MIT,
Rust) — a serial/UART MCP server with JSON macro automation and agent skills
for repeatable timed workflows. It covers the UART HAL class the way this
plugin covers JTAG/SWD/probe-rs, and is a reasonable follow-up wrap, but is out
of scope for wq#4139; tracked as a candidate for its own ticket rather than
folded into this one.

## License

MIT (upstream: Adancurusul/embedded-debugger-mcp)
