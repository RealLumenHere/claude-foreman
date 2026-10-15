"""Tests for ops/wq3943-limpet-vision-pipeline-a1/capture_ops.py.

No real ffmpeg, HAL script, or camera is exercised: subprocess.run is
monkeypatched throughout to record the argv it was given and return a canned
result, same pattern as
ops/wq3882-limpet-flashrom-mcp-a1/tests/test_flashrom_mcp.py.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import capture_ops as ops  # noqa: E402


class FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_resolve_device_passes_through_literal_path():
    assert ops.resolve_device("/dev/video3") == "/dev/video3"


def test_resolve_device_matches_by_hw_type(monkeypatch):
    monkeypatch.setattr(ops, "list_capture_devices", lambda: [
        {"hw_type": "webcam", "name": "Generic UVC webcam", "device_node": "/dev/video0"},
        {"hw_type": "hdmi_capture", "name": "Magewell", "device_node": "/dev/video1"},
    ])
    assert ops.resolve_device("hdmi_capture") == "/dev/video1"


def test_resolve_device_matches_by_name_substring(monkeypatch):
    monkeypatch.setattr(ops, "list_capture_devices", lambda: [
        {"hw_type": "webcam", "name": "Logitech C920", "device_node": "/dev/video2"},
    ])
    assert ops.resolve_device("logitech") == "/dev/video2"


def test_resolve_device_raises_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(ops, "list_capture_devices", lambda: [])
    with pytest.raises(ops.CaptureError, match="no connected webcam"):
        ops.resolve_device("webcam")


def test_list_capture_devices_dedupes_video_matches(monkeypatch):
    calls = []

    def fake_hal_json(*args):
        calls.append(args)
        if args == ("detect",):
            return [{"hw_type": "hdmi_capture", "name": "Magewell", "device_node": "/dev/video1"}]
        return [
            {"hw_type": "hdmi_capture", "name": "Magewell", "device_node": "/dev/video1"},
            {"hw_type": "webcam", "name": "Unidentified UVC video device", "device_node": "/dev/video0"},
        ]

    monkeypatch.setattr(ops, "_hal_json", fake_hal_json)
    devices = ops.list_capture_devices()
    assert [d["device_node"] for d in devices] == ["/dev/video1", "/dev/video0"]
    assert calls == [("detect",), ("detect-video",)]


def test_capture_frame_builds_argv_and_reports_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(ops, "resolve_device", lambda device: "/dev/video0")
    out = tmp_path / "frames" / "f1.jpg"

    def fake_run(args, **kwargs):
        Path(out).write_bytes(b"\xff\xd8fake-jpeg")
        return FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = ops.capture_frame("webcam", str(out))
    assert result["ok"] is True
    assert result["device_node"] == "/dev/video0"
    assert out.parent.is_dir()


def test_capture_frame_mjpeg_requests_two_warmup_frames(monkeypatch, tmp_path):
    monkeypatch.setattr(ops, "resolve_device", lambda device: "/dev/video0")
    out = tmp_path / "f.jpg"
    seen = []

    def fake_run(args, **kwargs):
        seen.append(args)
        out.write_bytes(b"\xff\xd8fake-jpeg")
        return FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    ops.capture_frame("webcam", str(out), input_format="mjpeg")
    argv = seen[0]
    assert "-input_format" in argv and argv[argv.index("-input_format") + 1] == "mjpeg"
    assert "-frames:v" in argv and argv[argv.index("-frames:v") + 1] == "2"


def test_capture_frame_default_is_single_frame(monkeypatch, tmp_path):
    monkeypatch.setattr(ops, "resolve_device", lambda device: "/dev/video0")
    out = tmp_path / "f.jpg"
    seen = []

    def fake_run(args, **kwargs):
        seen.append(args)
        out.write_bytes(b"\xff\xd8fake-jpeg")
        return FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    ops.capture_frame("webcam", str(out))
    argv = seen[0]
    assert argv[argv.index("-frames:v") + 1] == "1"
    assert "-input_format" not in argv


def test_capture_frame_nonzero_exit_reports_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(ops, "resolve_device", lambda device: "/dev/video0")
    out = tmp_path / "f.jpg"

    def fake_run(args, **kwargs):
        return FakeCompletedProcess(returncode=1, stderr="Cannot open video device\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = ops.capture_frame("webcam", str(out))
    assert result["ok"] is False
    assert "Cannot open video device" in result["stderr"]


def test_capture_frame_timeout_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(ops, "resolve_device", lambda device: "/dev/video0")

    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ops.CaptureError, match="timed out"):
        ops.capture_frame("webcam", str(tmp_path / "f.jpg"))


def test_hal_json_missing_script_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(ops, "HAL_SCRIPT", tmp_path / "does-not-exist.py")
    with pytest.raises(ops.CaptureError, match="HAL script not found"):
        ops._hal_json("detect")


def test_hal_json_parses_stdout(monkeypatch, tmp_path):
    hal = tmp_path / "hal.py"
    hal.write_text("#!/usr/bin/env python3\n")
    monkeypatch.setattr(ops, "HAL_SCRIPT", hal)

    def fake_run(command, **kwargs):
        assert command[-1] == "detect-video"
        return FakeCompletedProcess(returncode=0, stdout=json.dumps([{"hw_type": "webcam"}]))

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert ops._hal_json("detect-video") == [{"hw_type": "webcam"}]
