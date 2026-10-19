# limpet-plugin-serial-mcp

UART/serial automation plugin for Limpet, wrapping
[alxgmpr/serial-mcp](https://github.com/alxgmpr/serial-mcp) (MIT, PyPI package
`pyserial-mcp` v0.7.4, 24 tools, Beta).

## Corrected source (wq#4317)

The dispatching ticket named `github.com/dperezcabrera/pico-agent` as the
upstream to wrap. That repo exists but is **not** a UART tool: it is a
deprecated, general-purpose LangChain/LangGraph agent framework built on
Pico-IoC dependency injection (its own README opens "**DEPRECATED.** This
package is no longer maintained and receives no further releases"), with no
mention of UART, serial, picocom, or minicom anywhere in it. Confirmed via
`fleet-search web` on 2026-10-19 — see `kb/ISSUE-LOG.md`.

The underlying need (picocom/minicom are installed on Lumen with no MCP
wrapper; automated UART interaction is wanted for the Spectrio RE target host
and the Freedom Bot ESP32's serial link) is real, so this plugin wraps the
tool that actually does it — `alxgmpr/serial-mcp` — found via `fleet-search
web "MCP server serial port UART automation tool github"`.

## What it does

Exposes a serial port as MCP tools so an agent can:

- Discover connected USB-serial adapters by VID:PID (`list_serial_ports`)
- Open a session with configurable baud/data/stop/parity bits
- Send a command and wait for a regex-matched response (`serial_command`),
  with an optional triggered reply sent the instant the pattern matches
  (`respond`/`respond_hex`) — e.g. interrupt U-Boot autoboot or answer a
  bootloader handshake byte
- Read/write raw hex for binary protocols
- Drive DTR/RTS signal lines (`serial_set_signals`) — e.g. force an ESP32
  into bootloader/reset — and send a break condition
- Auto-detect baud rate by probing common rates
- Transfer files over XMODEM (checksum or CRC-16)
- Log a session to a file; manage multiple ports as separate sessions in one
  server process

## Energize gate

Upstream's own MCP `readOnlyHint`/`destructiveHint` annotations describe
side effects on the MCP session or host filesystem, not physical-device
safety, so this plugin does not pass them through as-is (same approach as
`limpet-plugin-buspirate-mcp`). For example upstream marks `serial_open`,
`serial_close`, and `serial_clear_history` `readOnlyHint=False` because they
mutate server-side session state. Conversely `serial_wait_for` (optional
`respond`/`respond_hex`, `server.py:747-748`) and `serial_detect_baud`
(`probe=True` writes `b"\r\n"`, `server.py:1075-1076`) can write to the
device despite looking like reads.

**`serial_open` writes to the wire too — it is gated.** (Corrected by wq#4323's
judgment pass; the PR as originally shipped claimed it was side-effect-free,
which was wrong.) `session.py`'s `serial.Serial(...)` call passes no
`dtr`/`rts`/`dsrdtr`/`rtscts` kwargs, so both the Linux kernel (`tty_port.c`
raises DTR+RTS on any open at a nonzero baud) and pyserial itself
(`serialposix.py Serial.open` → `_update_dtr_state`/`_update_rts_state`, both
defaulting true) assert DTR and RTS on the physical adapter the instant the
port opens. Effect depends on target wiring: resets a capacitor-coupled-DTR
board (Arduino/AVR-style) on every open; holds a target in reset for as long
as the port stays open if RTS is jumpered straight to an enable/reset pin (a
common bench RE hookup); no effect on a TX/RX/GND-only console tap; and — per
Espressif's boot-mode-selection docs — does **not** force a standard
two-transistor auto-program ESP32 dev board (DevKitC/NodeMCU-style) into
bootloader, since that circuit ignores DTR and RTS asserted together. Only
`serial_set_signals`'s independent DTR/RTS sequencing can do that.
`serial_close` stays ungated despite also dropping both lines (via HUPCL) on
close, because the inactivity reaper (900s default) and the server's `atexit`
handler already drop them with no tool call at all — gating close would not
add protection open already bypasses.

- **Gated:** `serial_execute`, `serial_command`, `serial_wait_for`,
  `serial_write`, `serial_write_hex`, `serial_set_signals`,
  `serial_send_break`, `serial_detect_baud`, `serial_xmodem_send`,
  `serial_xmodem_receive`, `serial_force_release`, `serial_open`
- **Ungated:** `list_serial_ports`, `serial_close`,
  `serial_change_settings`, `serial_read`, `serial_read_since`,
  `serial_read_hex`, `serial_get_signals`, `serial_clear_history`,
  `serial_log_start`, `serial_log_stop`, `serial_list_sessions`,
  `serial_status`

Before opening a port against an unfamiliar target, confirm the wiring
(TX/RX/GND-only vs DTR/RTS connected) — see `agent_hints.when_not_to_use` in
`limpet-plugin.toml`.

## Hardware

Works with any USB-serial adapter already in
`limpet/hardware.d/05-usb-serial.toml` (FTDI FT232R/FT2232H, Silicon Labs
CP2102/CP2104, WCH CH340/CH341 UART mode, Prolific PL2303) — all in-kernel
drivers, zero DKMS. No device-specific `hardware/` catalog entry is added
here since this plugin isn't tied to one probe model (contrast
`limpet-plugin-jlink-mcp`'s `PROBE_TYPE` backend selection).

## Installation

```bash
./install.sh
```

Requires: Python 3.10+. Installs `pyserial-mcp==0.7.4` into a dedicated venv
under `/opt/limpet/plugins/serial-mcp`.

## License

MIT (upstream: alxgmpr/serial-mcp). Confirmed via PyPI package metadata
(`License-Expression: MIT`, bundled `LICENSE` file) for `pyserial-mcp`
0.7.4.
