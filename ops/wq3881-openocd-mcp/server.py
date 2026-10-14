#!/usr/bin/env python3
"""openocd-mcp — FastMCP wrapper over OpenOCD's Tcl RPC interface (wq#3881).

Gives an agent JTAG/SWD control without CLI scripting: connect/disconnect a
target, halt/resume/reset, read/write memory, flash firmware, enumerate the
JTAG chain, and drive raw boundary-scan shifts. stdio transport (one child
process per debug session, same shape as the ghidra-mcp/radare2 placeholder
entries in ops/wq3808-limpet-mcp-meta-server-a1/manifest.example.json) since
a JTAG/SWD probe is exclusive hardware, not a shared service.

All protocol logic lives in openocd_client.py, which has zero MCP dependency
so it can be unit-tested in a sandbox without the `mcp` package installed
(see tests/test_openocd_client.py). This file is intentionally a thin
pass-through: one tool per OpenOCDClient method, JSON-friendly return types.
"""

from __future__ import annotations

try:  # mcp SDK 1.x layout (pin matches ops/wq1151-lumen-kb/requirements.txt)
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - 2.x layout, verify on migration
    from mcp.server import FastMCP  # type: ignore[no-redef,attr-defined]

from openocd_client import OpenOCDClient

mcp = FastMCP("openocd")
_client = OpenOCDClient()


@mcp.tool()
def connect_target(config_files: list[str], tcl_port: int = 6666,
                    telnet_port: int = 4444, gdb_port: int = 3333) -> str:
    """Launch OpenOCD with the given -f config files (interface cfg then target cfg)
    and open its Tcl RPC socket. Call this before any other tool."""
    _client._tcl_port = tcl_port
    _client._telnet_port = telnet_port
    _client._gdb_port = gdb_port
    return _client.connect(config_files)


@mcp.tool()
def disconnect_target() -> str:
    """Close the Tcl RPC link and terminate the OpenOCD process."""
    return _client.disconnect()


@mcp.tool()
def halt_target() -> str:
    """Halt the target core."""
    return _client.target_halt()


@mcp.tool()
def resume_target(address: int | None = None) -> str:
    """Resume the target core, optionally at a given PC address."""
    return _client.target_resume(address)


@mcp.tool()
def reset_target(mode: str = "run") -> str:
    """Reset the target. mode is one of: run, halt, init."""
    return _client.target_reset(mode)


@mcp.tool()
def read_target_memory(address: int, width: int = 32, count: int = 1, phys: bool = False) -> list[int]:
    """Read `count` memory cells of `width` bits (8/16/32/64) starting at `address`."""
    return _client.read_memory(address, width=width, count=count, phys=phys)


@mcp.tool()
def write_target_memory(address: int, data: list[int], width: int = 32, phys: bool = False) -> str:
    """Write `data` as `width`-bit cells starting at `address`."""
    return _client.write_memory(address, data, width=width, phys=phys)


@mcp.tool()
def flash_firmware(image_path: str, address: int, erase: bool = True, verify: bool = True) -> str:
    """Flash a firmware image at `address`, erasing first and verifying after by default."""
    return _client.flash_write(image_path, address, erase=erase, verify=verify)


@mcp.tool()
def scan_jtag_chain() -> list[dict]:
    """Enumerate the JTAG chain: one dict per detected TAP (name, idcode, ir_len, ...)."""
    return _client.scan_chain()


@mcp.tool()
def jtag_tap_names() -> list[str]:
    """List configured JTAG TAP names."""
    return _client.jtag_names()


@mcp.tool()
def boundary_scan(tap: str, ir_value: int, dr_length: int) -> str:
    """Raw boundary-scan shift: load `ir_value` into `tap`'s instruction register via
    irscan, then shift `dr_length` zero bits through its data register via drscan."""
    return _client.boundary_scan(tap, ir_value, dr_length)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
