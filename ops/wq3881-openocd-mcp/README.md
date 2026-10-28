# openocd-mcp (wq#3881)

FastMCP stdio server giving an agent JTAG/SWD control over OpenOCD without
CLI scripting: connect/disconnect a target, halt/resume/reset, read/write
memory, flash firmware, enumerate the JTAG chain, and drive raw
boundary-scan shifts.

OpenOCD (0.12.0, confirmed on the maksoor RE workstation — see
`TOOL_REGISTRY.md`) is driven over its Tcl RPC port (default 6666), the same
command set as its interactive telnet console, one command per request, each
response terminated by a single 0x1a byte (OpenOCD User's Guide, "Tcl RPC
server"). `read_memory`/`write_memory` are used instead of the older
mdw/mww text commands — they return a plain Tcl list instead of an
addr-prefixed hex dump that would need its own parser.

## Layout

- `openocd_client.py` — stdlib-only driver (subprocess + socket). No `mcp`
  import, so it is independently unit-testable without the MCP SDK.
- `server.py` — thin FastMCP wrapper: one `@mcp.tool()` per client method.
- `tests/fake_openocd.py` — a fake `openocd` binary that speaks the real Tcl
  RPC wire protocol over a real loopback socket, used by the test suite so
  connect()/disconnect() and every command are exercised against real
  process + socket machinery rather than a mock.
- `tests/test_openocd_client.py` — 17 tests covering the connect/disconnect
  lifecycle, every tool's happy path, and invalid-input rejection (bad
  width, bad reset mode, not-yet-connected, empty write, missing flash
  image, non-positive scan length) per wq#2630 (a probe must be able to
  fail, not just confirm the designed-for path).

## Tools

| Tool | Maps to |
|---|---|
| `connect_target(config_files, tcl_port, telnet_port, gdb_port)` | launch OpenOCD with `-f` config files, open Tcl RPC |
| `disconnect_target()` | close RPC link, terminate the OpenOCD process |
| `halt_target()` | `halt` |
| `resume_target(address?)` | `resume [address]` |
| `reset_target(mode)` | `reset {run,halt,init}` |
| `read_target_memory(address, width, count, phys?)` | `read_memory` |
| `write_target_memory(address, data, confirm_token="", width, phys?)` | `write_memory` — **energizing (hal-spec.md §9.2a), refuses without a valid operator-issued `confirm_token`**, wq#4747 |
| `flash_firmware(image_path, address, confirm_token="", erase?, verify?)` | `flash write_image` (+ `verify_image`) — **energizing, same confirm_token gate** |
| `scan_jtag_chain()` | `scan_chain`, parsed into one dict per TAP |
| `jtag_tap_names()` | `jtag names` |
| `boundary_scan(tap, ir_value, dr_length)` | `irscan` then `drscan` |

## Config files

`connect_target` takes real OpenOCD `-f` config paths, e.g.
`["interface/stlink.cfg", "target/stm32f4x.cfg"]` from OpenOCD's own
`/usr/share/openocd/scripts/` tree — this server doesn't ship or guess
board configs, it passes through whatever the caller names.

## Running the tests

```
python3 -I -m pytest tests/test_openocd_client.py -q
```

No real probe or OpenOCD binary needed — `fake_openocd.py` stands in for
both. `server.py` itself is not covered by this suite: the `mcp` package
(`requirements.txt`: `mcp>=1.9,<2`) is not installed in the build sandbox
(`pip install` isn't on the worker Bash allowlist), the same constraint
`ops/wq1151-lumen-kb/requirements.txt` documents for its own server.py. The
thin wrapper in server.py has no logic of its own to diverge from
`openocd_client.py`'s tested behavior — each tool is a one-line pass-through.
