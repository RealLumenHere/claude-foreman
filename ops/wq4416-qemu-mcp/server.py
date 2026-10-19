#!/usr/bin/env python3
"""qemu-mcp — FastMCP wrapper over QEMU's QMP control socket (wq#4416).

Gives an agent eyes (qemu_screenshot for the guest framebuffer; qemu_serial /
qemu_wait_serial for the serial console — the sensor that still sees boot
errors when the framebuffer is still black) and hands (qemu_type, plus
qemu_boot/qemu_quit for session lifecycle) on one QEMU VM, while the VM's own
`-vnc :N,share=allowed` flag (set by qemu_boot) lets Frank attach an ordinary
VNC viewer to the same display at the same time — neither observer evicts the
other. Built for Limpet ISO install/boot testing per
projects/limpet/docs/iso-test-pipeline.md (wq#4417 design, sections 3-4);
this ticket is the "new component" that design's open items (section 11)
calls out as not yet existing.

stdio transport, one child process per VM session — same shape as
ops/wq3881-openocd-mcp/server.py (one exclusive piece of state per server
instance, not a shared service).

All protocol logic lives in qemu_client.py, which has zero MCP dependency so
it can be unit-tested in a sandbox without the `mcp` package installed (see
tests/test_qemu_client.py). This file is intentionally a thin pass-through:
one tool per QMPClient method, JSON-friendly return types.
"""

from __future__ import annotations

try:  # mcp SDK 1.x layout (pin matches ops/wq1151-lumen-kb/requirements.txt)
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - 2.x layout, verify on migration
    from mcp.server import FastMCP  # type: ignore[no-redef,attr-defined]

from qemu_client import QMPClient

mcp = FastMCP("qemu")
_client = QMPClient()


@mcp.tool()
def qemu_boot(
    iso_path: str,
    run_dir: str,
    memory_mb: int = 4096,
    smp: int = 2,
    machine: str = "q35",
    vnc_display: int = 1,
) -> dict:
    """Launch qemu-system-x86_64 booting `iso_path` with a shared VNC display
    (Frank can attach a viewer to localhost:5900+vnc_display at the same
    time), a serial console logged to run_dir/serial.log, and a QMP control
    socket at run_dir/qmp.sock. Call this before any other tool."""
    return _client.boot(
        iso_path, run_dir, memory_mb=memory_mb, smp=smp, machine=machine, vnc_display=vnc_display
    )


@mcp.tool()
def qemu_screenshot(out_path: str, image_format: str = "png") -> str:
    """Capture the guest framebuffer to `out_path` via QMP screendump."""
    return _client.screenshot(out_path, image_format=image_format)


@mcp.tool()
def qemu_serial(max_chars: int | None = None) -> str:
    """Return serial console output produced since the last qemu_serial /
    qemu_wait_serial call (tails the log, never re-reads bytes already
    returned)."""
    return _client.read_serial(max_chars=max_chars)


@mcp.tool()
def qemu_wait_serial(pattern: str, timeout_s: float = 60.0, regex: bool = False) -> dict:
    """Poll the serial console until `pattern` appears (or `timeout_s`
    elapses). This is the sensor that sees boot errors while the framebuffer
    is still black — e.g. qemu_wait_serial('login:') for a successful boot,
    or a panic marker to catch a kernel crash before anything reaches the
    VNC display."""
    return _client.wait_serial(pattern, timeout_s=timeout_s, regex=regex)


@mcp.tool()
def qemu_type(text: str, key_delay_s: float = 0.05) -> str:
    """Type `text` into the guest via QMP send-key, one chord per
    character."""
    return _client.type_text(text, key_delay_s=key_delay_s)


@mcp.tool()
def qemu_quit() -> str:
    """Send QMP `quit` and terminate the QEMU process."""
    return _client.quit()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
