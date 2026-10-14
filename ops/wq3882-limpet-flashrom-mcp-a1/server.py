#!/usr/bin/env python3
"""limpet-flashrom-mcp — FastMCP wrapper around `flashrom` (wq#3882).

Tools: detect_chip, read_flash, write_flash, verify_flash, erase_chip,
identify_programmer. Pattern follows ops/wq1151-lumen-kb/server.py (FastMCP,
one @mcp.tool() per operation). All the actual logic — argv construction,
subprocess handling, output parsing — lives in flashrom_ops.py so it's testable
without the `mcp` package installed; this file only adds the MCP decoration.

flashrom is catalogued at /usr/sbin/flashrom on the maksoor RE host
(TOOL_REGISTRY.md). This server is meant to run there (or wherever a flash
programmer is physically attached), registered as a `stdio` child in the
LIMPET meta-server's manifest (ops/wq3808-limpet-mcp-meta-server-a1), or run
standalone.

Firmware extraction workflow this exists for: identify_programmer (what's
plugged in) -> detect_chip (what chip is on the other end of -p) -> read_flash
(dump before touching anything) -> ... -> write_flash -> verify_flash.
erase_chip is destructive and refuses to run without confirm=True.
"""
from __future__ import annotations

try:  # mcp SDK 1.x layout (current fleet pin: mcp>=1.9,<2), matches wq1151
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - 2.x layout, verify on migration
    from mcp.server import FastMCP  # type: ignore[no-redef,attr-defined]

import flashrom_ops as ops

mcp = FastMCP("limpet-flashrom")


@mcp.tool()
def detect_chip(programmer: str, chip: str = "") -> dict:
    """Probe the flash chip wired to `programmer` (e.g. 'ch341a_spi',
    'linux_spi:dev=/dev/spidev0.0') without reading, writing, or erasing anything —
    flashrom's default action when no -r/-w/-E/-v flag is given. Run this first to
    confirm the chip is recognized and to get its exact name for `chip=` on later calls.
    """
    return ops.detect_chip(programmer, chip)


@mcp.tool()
def read_flash(
    programmer: str, output_path: str, chip: str = "", region: str = "", layout_file: str = ""
) -> dict:
    """Read the full flash chip (or one named `region`, via flashrom's -i) attached via
    `programmer` and save it to `output_path`. Always do this before write_flash/erase_chip
    — it's the only way to recover the original image if something goes wrong.
    `region` requires `layout_file` (a path to a flashrom -l layout file naming the
    region) — flashrom rejects -i without a layout source.
    """
    return ops.read_flash(programmer, output_path, chip, region, layout_file)


@mcp.tool()
def write_flash(programmer: str, input_path: str, chip: str = "", verify: bool = True) -> dict:
    """Write `input_path` to the flash chip attached via `programmer`. Verifies against
    the written image automatically unless `verify=False` (flashrom's -n/--noverify).
    Read the chip first (read_flash) and keep that backup — this overwrites the chip.
    """
    return ops.write_flash(programmer, input_path, chip, verify)


@mcp.tool()
def verify_flash(programmer: str, input_path: str, chip: str = "") -> dict:
    """Verify the flash chip attached via `programmer` matches `input_path`, without
    writing anything (flashrom's -v)."""
    return ops.verify_flash(programmer, input_path, chip)


@mcp.tool()
def erase_chip(programmer: str, confirm: bool = False, chip: str = "") -> dict:
    """Erase the flash chip attached via `programmer` (flashrom's -E). DESTRUCTIVE and
    irreversible without a prior backup: refuses to run unless `confirm=True` is passed
    explicitly. Read the chip first (read_flash) — there is no undo.
    """
    return ops.erase_chip(programmer, confirm, chip)


@mcp.tool()
def identify_programmer() -> list[dict]:
    """Report which flash programmers (CH341A, TL866/XGecu, etc.) are physically
    connected right now, via the LIMPET hardware-abstraction layer's USB VID:PID
    detection (wq#3809). Call this before detect_chip to confirm a programmer is
    actually plugged in and get its device node, instead of guessing the
    `programmer=` value.
    """
    return ops.identify_programmer()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
