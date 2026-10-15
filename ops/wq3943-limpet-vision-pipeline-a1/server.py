#!/usr/bin/env python3
"""limpet-vision-mcp — FastMCP wrapper for the LIMPET vision component-ID
pipeline (wq#3943).

Tools: limpet_capture_frame, limpet_identify_components, limpet_component_lookup,
limpet_component_manifest. Pattern follows
ops/wq3882-limpet-flashrom-mcp-a1/server.py (FastMCP, logic split into plain
modules so it's testable without the `mcp` package installed).

Pipeline design: design/wq3900_limpet_vision_component_id.md. Model/device
research: research/WQ3922_VISION_HDMI_CAPTURE.md. Target Device Wizard
integration contract: docs/target-wizard-spec.md §4.2 (see
manifest_ops.wizard_candidates).
"""
from __future__ import annotations

import time

try:  # mcp SDK 1.x layout (current fleet pin: mcp>=1.9,<2), matches wq1151
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - 2.x layout, verify on migration
    from mcp.server import FastMCP  # type: ignore[no-redef,attr-defined]

import capture_ops
import component_lookup_ops
import manifest_ops
import vision_ops

mcp = FastMCP("limpet-vision")


@mcp.tool()
def limpet_capture_frame(device: str, output_path: str = "") -> dict:
    """Capture one still frame from `device` (a HAL logical name — "webcam",
    "hdmi_capture" — or a literal /dev/videoN path) via ffmpeg/v4l2. Defaults
    `output_path` to a timestamped file under /tmp/limpet-captures/ when not given.
    """
    out = output_path or f"/tmp/limpet-captures/capture-{int(time.time())}.jpg"
    return capture_ops.capture_frame(device, out)


@mcp.tool()
def limpet_identify_components(image_path: str) -> dict:
    """Read `image_path` with the local vision model and return a verbatim
    per-component transcription (bbox, package, markings, type_guess,
    confidence) — the read stage only, no part-identity resolution. Call
    limpet_component_lookup on each marking to resolve a part identity.
    """
    return vision_ops.identify_components(image_path)


@mcp.tool()
def limpet_component_lookup(marking: str, package: str = "") -> dict:
    """Resolve a transcribed chip marking to a part identity through the
    resolver chain (flash-marking decode, then Part-DB if configured).
    Returns resolved=None rather than a guess when nothing matches confidently.
    """
    return component_lookup_ops.lookup_part(marking, package)


@mcp.tool()
def limpet_component_manifest(workspace: str, frames: list[str] | None = None, device: str = "") -> dict:
    """Run the full pipeline (capture if needed -> vision read -> resolve)
    over `frames` (existing image paths) or a fresh `device` capture, and
    write/return the components.json manifest in `<workspace>/evidence/`.
    """
    return manifest_ops.run_pipeline(workspace, frames=frames, device=device)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
