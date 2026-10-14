#!/usr/bin/env python3
"""sigrok-mcp -- FastMCP wrapper around sigrok-cli for logic-analyzer work (wq#3883).

Tools: list_devices, device_options, configure_samplerate, capture, decode,
export. Pairs with Sootty (wq#3878), which renders the VCD this server's
export tool produces as a terminal waveform. argv lists only, shell=False
throughout (sigrok_cli.py builds argv, runner.py executes it) -- see
runner.py docstring.
"""

from __future__ import annotations

import os

try:  # mcp SDK 1.x layout (current pin: mcp>=1.9,<2), matches wq#1151's server.py
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - 2.x layout, verify on migration
    from mcp.server import FastMCP  # type: ignore[no-redef,attr-defined]

import runner
import sigrok_cli

HOST = os.environ.get("SIGROK_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("SIGROK_MCP_PORT", "8782"))
CAPTURE_TIMEOUT = int(os.environ.get("SIGROK_MCP_CAPTURE_TIMEOUT", "120"))

mcp = FastMCP("sigrok", host=HOST, port=PORT)


@mcp.tool()
def list_devices(driver: str = "", conn: str = "") -> list:
    """Scan for sigrok-supported logic analyzers. Call first to find a device's driver/conn."""
    argv = sigrok_cli.scan_argv(driver=driver, conn=conn)
    proc = runner.run(argv, timeout=30)
    return sigrok_cli.parse_scan_output(proc.stdout)


@mcp.tool()
def device_options(driver: str, conn: str = "") -> dict:
    """Show supported config options (samplerates, channels, ...) for one driver."""
    argv = sigrok_cli.show_argv(driver=driver, conn=conn)
    proc = runner.run(argv, timeout=30)
    return {"driver": driver, "raw": proc.stdout}


@mcp.tool()
def configure_samplerate(samplerate: str) -> dict:
    """Normalize a samplerate ('1MHz', '500kHz', 1000000, ...) into the Hz value capture() needs."""
    hz = sigrok_cli.normalize_samplerate(samplerate)
    return {"samplerate_hz": hz, "config_arg": f"samplerate={hz}"}


@mcp.tool()
def capture(
    driver: str,
    samplerate: str,
    output_path: str,
    conn: str = "",
    channels: str = "",
    samples: str = "",
    duration: str = "",
    output_format: str = "",
) -> dict:
    """Capture from a logic analyzer. Give samples ('1000') or duration ('2s'), not both.

    output_format defaults to sigrok's native .sr session, the format
    decode() and export() expect as input.
    """
    hz = sigrok_cli.normalize_samplerate(samplerate)
    argv = sigrok_cli.capture_argv(
        driver, hz, output_path, conn=conn, channels=channels,
        samples=samples, duration=duration, output_format=output_format,
    )
    runner.run(argv, timeout=CAPTURE_TIMEOUT)
    exists = os.path.isfile(output_path)
    return {
        "output_path": output_path,
        "samplerate_hz": hz,
        "exists": exists,
        "size_bytes": os.path.getsize(output_path) if exists else 0,
    }


@mcp.tool()
def decode(input_file: str, decoders: str, annotations: str = "") -> list:
    """Decode a captured .sr session with libsigrokdecode protocol decoders.

    decoders e.g. 'spi:miso=1:mosi=5:clk=3:cs=0', 'uart:rx=0:tx=1', 'i2c', or 'jtag'.
    """
    argv = sigrok_cli.decode_argv(input_file, decoders, annotations=annotations)
    proc = runner.run(argv, timeout=60)
    return sigrok_cli.parse_decode_output(proc.stdout)


@mcp.tool()
def export(input_file: str, output_path: str, output_format: str) -> dict:
    """Re-export a captured .sr session into another format, e.g. 'vcd' or 'csv'."""
    argv = sigrok_cli.export_argv(input_file, output_path, output_format)
    runner.run(argv, timeout=60)
    exists = os.path.isfile(output_path)
    return {
        "output_path": output_path,
        "format": output_format,
        "exists": exists,
        "size_bytes": os.path.getsize(output_path) if exists else 0,
    }


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
