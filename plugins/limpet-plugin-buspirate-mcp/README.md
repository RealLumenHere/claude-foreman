# limpet-plugin-buspirate-mcp

Multi-protocol bus plugin for Limpet, wrapping
[mplogas/buspirate-mcp](https://github.com/mplogas/buspirate-mcp) (MIT, pinned
commit `36589d5d9fbcb3018f8568a88cdad04a7c434299`, 33 tools) for the
**Bus Pirate 6 only** (BPIO2 FlatBuffers interface — not v3/v5).

## What it does

Talks to a Bus Pirate 6 over its BPIO2 binary protocol (vendored as the
`vendor/bpio2` git submodule, `DangerousPrototypes/BusPirate-BPIO2-flatbuffer-interface`)
to give an MCP client UART, SPI, I2C, 1-Wire, and FALA logic-analyzer access to
whatever's wired to the Bus Pirate, plus target PSU control and ESP32
download-mode flashing (`esptool`, a hard upstream dependency even if unused).

This fills the HAL classes earlier wrapped plugins leave empty: SPI-flash
dump, I2C/EEPROM, 1-Wire, and UART console. It partially covers the
flashrom-mcp gap from wq#4096 (as the SPI programmer only, not flashrom's chip
database or other programmers) — the first-party flashrom-mcp build stays on
the books.

## Do not trust upstream's own safety tiers

Upstream's `safety.py` defines read-only / allowed-write / approval-write
tiers. **This packaging does not rely on them.** Per
`reports/wq4145_limpet_verify_hackrf_mcp_eval_buspirate_mcp.md` section 2:

1. The "approval-write" tier is gated by an ordinary tool argument
   (`_confirmed=true`, `server.py:184,201,363,472`) that the calling model can
   set itself — it is a speed bump, not a human gate.
2. `open_spi`, `open_i2c`, and `open_1wire` are labelled "allowed-write" but
   can energize the target PSU via `voltage_mv` with no `_confirmed` step at
   all (`hardware.py:220-223,259-262,295-298`).
3. `spi_transfer` and `la_command` are labelled "read-only" but send arbitrary
   bytes to the bus — `spi_transfer` can issue WREN + chip-erase
   (`tools.py:1244-1256`, opcodes at `tools.py:50,53`); `la_command`'s
   validator only checks ASCII/length/bracket-balance, not content
   (`tools.py:1339-1357`).

The `[mcp.tools_energize]` map in `limpet-plugin.toml` is this packaging's own
judgment call, built from reading the code, not a passthrough of upstream's
tiers. See that file's header and table for the full gated/ungated list.

## Hardware

Bus Pirate 6 only. VID:PID `1209:7331`, confirmed from the
`DangerousPrototypes/BusPirate5-firmware` source (which also builds the BP6 —
`src/platform/bpi6-rev2.c/h` live in that same tree): `src/usb_descriptors.c:32-37`
documents two pid.codes-registered pairs for this firmware family and states
the firmware is hard-coded to use the first (`1209:7331`), identical to the
existing Bus Pirate v5 entry. See `limpet/hardware.d/03-multiprotocol-tools.toml`
and `assets/90-limpet-buspirate-mcp.rules`.

## Install

`install.sh` clones upstream with `--recurse-submodules` (plain `pip install .`
misses `vendor/bpio2`), pins to `36589d5d9fbcb3018f8568a88cdad04a7c434299`,
and installs into a dedicated venv under `/opt/limpet/plugins/buspirate-mcp/`
along with `esptool>=4.0`. There is no PyPI release to pin to instead.
