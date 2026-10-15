"""wq#3943 — v4l2 still-frame capture for the LIMPET vision pipeline.

Resolves a logical device (a HAL hw_type like "webcam"/"hdmi_capture", or a
literal /dev/videoN path) to a device node via the HAL (wq#3809's hal.py plus
this ticket's hal.detect_video_devices() for generic UVC webcams/dongles,
which carry no stable VID:PID — research/WQ3922_VISION_HDMI_CAPTURE.md §G-b),
then shells out to ffmpeg for a single still frame. Split from server.py so
this is testable without the `mcp` package (same pattern as
ops/wq3882-limpet-flashrom-mcp-a1/flashrom_ops.py).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

FFMPEG_BIN = os.environ.get("LIMPET_FFMPEG_BIN") or shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
DEFAULT_TIMEOUT_SEC = 30

HAL_SCRIPT = Path(os.environ.get(
    "LIMPET_HAL_SCRIPT", "/home/lumen/foreman/ops/wq3809-limpet-hal/hal.py"
))

# research/WQ3922_VISION_HDMI_CAPTURE.md §2b/§G-c: USB-2 MS2109-class dongles only
# deliver HD over MJPEG, and the first MJPEG frame off a cold device is frequently
# garbage — request two frames and let the second overwrite the first (ffmpeg's
# -update 1 image2 behaviour), matching that doc's exact capture snippet.
_MJPEG_WARMUP_FRAMES = 2


class CaptureError(RuntimeError):
    pass


def _hal_json(*args: str) -> list[dict]:
    if not HAL_SCRIPT.is_file():
        raise CaptureError(f"LIMPET HAL script not found at {HAL_SCRIPT} (set LIMPET_HAL_SCRIPT)")
    try:
        proc = subprocess.run(
            [sys.executable, str(HAL_SCRIPT), *args],
            capture_output=True, text=True, timeout=15, shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CaptureError(f"hal.py {' '.join(args)} timed out after 15s") from exc
    if proc.returncode != 0:
        raise CaptureError(f"hal.py {' '.join(args)} failed (exit {proc.returncode}): {proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise CaptureError(f"hal.py {' '.join(args)} produced non-JSON output: {proc.stdout[:200]!r}") from exc


def list_capture_devices() -> list[dict]:
    """Every webcam/HDMI-capture device the HAL currently sees: VID:PID-matched
    units from `hal.py detect` plus generic UVC webcams/HDMI dongles from
    `hal.py detect-video` (no stable VID:PID), with the second list filtered
    so a VID:PID-matched unit is never reported twice."""
    usb_matches = [d for d in _hal_json("detect") if d.get("hw_type") in ("webcam", "hdmi_capture")]
    known_nodes = {d["device_node"] for d in usb_matches}
    video_matches = [d for d in _hal_json("detect-video") if d.get("device_node") not in known_nodes]
    return usb_matches + video_matches


def resolve_device(device: str) -> str:
    """Resolve `device` to a /dev/videoN node. Accepts a literal device path
    (passed through unchanged) or a logical name: a hw_type ("webcam",
    "hdmi_capture") returning the first connected match, or a substring match
    against a device's `name` for picking among several of the same hw_type."""
    if device.startswith("/dev/"):
        return device
    candidates = list_capture_devices()
    for entry in candidates:
        if entry.get("hw_type") == device:
            return entry["device_node"]
    for entry in candidates:
        if device.lower() in entry.get("name", "").lower():
            return entry["device_node"]
    raise CaptureError(
        f"no connected webcam/HDMI-capture device matches {device!r} "
        f"(saw: {[e.get('name') for e in candidates]})"
    )


def capture_frame(
    device: str,
    output_path: str,
    video_size: str = "1920x1080",
    input_format: str = "",
) -> dict:
    """Capture one still frame from `device` (a HAL logical name or a literal
    /dev/videoN) to `output_path` via ffmpeg. `input_format="mjpeg"` is
    required for USB-2 MS2109-class dongles to get HD
    (research/WQ3922_VISION_HDMI_CAPTURE.md §2b/§G-c) and triggers the
    two-frame MJPEG warmup discard."""
    node = resolve_device(device)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    frames = _MJPEG_WARMUP_FRAMES if input_format == "mjpeg" else 1
    args = [FFMPEG_BIN, "-f", "v4l2"]
    if input_format:
        args += ["-input_format", input_format]
    args += ["-video_size", video_size, "-i", node, "-frames:v", str(frames), "-update", "1", "-y", str(out)]

    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=DEFAULT_TIMEOUT_SEC, shell=False)
    except FileNotFoundError as exc:
        raise CaptureError(f"ffmpeg binary not found at {FFMPEG_BIN!r}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CaptureError(f"ffmpeg timed out after {DEFAULT_TIMEOUT_SEC}s capturing {node}") from exc

    return {
        "ok": proc.returncode == 0 and out.is_file(),
        "returncode": proc.returncode,
        "device_node": node,
        "output_path": str(out),
        "stderr": proc.stderr[-2000:],
    }
