"""Tests for ops/wq3943-limpet-vision-pipeline-a1/manifest_ops.py.

vision_ops.identify_components and component_lookup_ops.lookup_part are
monkeypatched to canned results — this tests manifest assembly/schema and
the workspace write, not the VLM or Part-DB themselves (covered by their own
test files).
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import component_lookup_ops  # noqa: E402
import manifest_ops  # noqa: E402
import vision_ops  # noqa: E402


def _frame(tmp_path, name="frame.jpg"):
    f = tmp_path / name
    f.write_bytes(b"\xff\xd8fake-jpeg-bytes")
    return f


def test_run_pipeline_requires_frames_or_device(tmp_path):
    with pytest.raises(ValueError, match="frames"):
        manifest_ops.run_pipeline(str(tmp_path))


def test_run_pipeline_resolved_component_lands_in_components(tmp_path, monkeypatch):
    frame = _frame(tmp_path)
    monkeypatch.setattr(vision_ops, "identify_components", lambda path, model=None: {
        "ok": True, "model": "qwen2.5-vl:7b", "error": None,
        "components": [{"bbox": [1, 2, 3, 4], "package": "SOIC-8", "markings": ["W25Q64JVSIQ"], "confidence": 0.9}],
    })
    monkeypatch.setattr(component_lookup_ops, "lookup_part", lambda marking, package="": {
        "resolved": {"manufacturer": "Winbond", "mpn": marking}, "candidates": [], "needs_review": False,
    })

    manifest = manifest_ops.run_pipeline(str(tmp_path), frames=[str(frame)])
    assert manifest["schema"] == manifest_ops.SCHEMA
    assert len(manifest["components"]) == 1
    assert manifest["components"][0]["part"]["manufacturer"] == "Winbond"
    assert manifest["unresolved"] == []
    assert manifest["board"]["frames"][0]["path"] == str(frame)
    assert len(manifest["board"]["frames"][0]["sha256"]) == 64


def test_run_pipeline_unresolved_marking_is_honest(tmp_path, monkeypatch):
    frame = _frame(tmp_path)
    monkeypatch.setattr(vision_ops, "identify_components", lambda path, model=None: {
        "ok": True, "model": "qwen2.5-vl:7b", "error": None,
        "components": [{"bbox": [1, 2, 3, 4], "package": "SOT-23", "markings": ["??2"], "confidence": 0.4}],
    })
    monkeypatch.setattr(component_lookup_ops, "lookup_part", lambda marking, package="": {
        "resolved": None, "candidates": [], "needs_review": True,
    })

    manifest = manifest_ops.run_pipeline(str(tmp_path), frames=[str(frame)])
    assert manifest["components"] == []
    assert manifest["unresolved"] == [{"id": f"{frame.stem}-0", "markings": ["??2"]}]


def test_run_pipeline_vision_failure_is_recorded_not_raised(tmp_path, monkeypatch):
    frame = _frame(tmp_path)
    monkeypatch.setattr(vision_ops, "identify_components", lambda path, model=None: {
        "ok": False, "model": "qwen2.5-vl:7b", "error": "ollama unreachable", "components": [],
    })
    manifest = manifest_ops.run_pipeline(str(tmp_path), frames=[str(frame)])
    assert manifest["components"] == []
    assert manifest["unresolved"] == [{"frame": str(frame), "error": "ollama unreachable"}]


def test_run_pipeline_writes_components_json(tmp_path, monkeypatch):
    frame = _frame(tmp_path)
    monkeypatch.setattr(vision_ops, "identify_components", lambda path, model=None: {
        "ok": True, "model": "x", "error": None, "components": [],
    })
    manifest_ops.run_pipeline(str(tmp_path), frames=[str(frame)])
    written = json.loads((tmp_path / "evidence" / "components.json").read_text())
    assert written["schema"] == manifest_ops.SCHEMA


def test_run_pipeline_captures_from_device_when_no_frames(tmp_path, monkeypatch):
    import capture_ops

    captured_to = {}

    def fake_capture_frame(device, output_path, **kwargs):
        captured_to["path"] = output_path
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"\xff\xd8fake-jpeg-bytes")
        return {"ok": True, "output_path": output_path}

    monkeypatch.setattr(capture_ops, "capture_frame", fake_capture_frame)
    monkeypatch.setattr(vision_ops, "identify_components", lambda path, model=None: {
        "ok": True, "model": "x", "error": None, "components": [],
    })
    manifest_ops.run_pipeline(str(tmp_path), device="webcam")
    assert captured_to["path"]


def test_run_pipeline_raises_when_capture_fails(tmp_path, monkeypatch):
    import capture_ops

    monkeypatch.setattr(capture_ops, "capture_frame", lambda device, output_path, **k: {
        "ok": False, "stderr": "no such device", "output_path": output_path,
    })
    with pytest.raises(RuntimeError, match="no such device"):
        manifest_ops.run_pipeline(str(tmp_path), device="webcam")


def test_wizard_candidates_shape_matches_wizard_contract(tmp_path, monkeypatch):
    frame = _frame(tmp_path)
    monkeypatch.setattr(vision_ops, "identify_components", lambda path, model=None: {
        "ok": True, "model": "x", "error": None,
        "components": [{"bbox": [1, 2, 3, 4], "package": "SOIC-8", "markings": ["W25Q64JVSIQ"], "confidence": 0.8}],
    })
    monkeypatch.setattr(component_lookup_ops, "lookup_part", lambda marking, package="": {
        "resolved": {"manufacturer": "Winbond", "mpn": marking}, "candidates": [], "needs_review": False,
    })
    candidates = manifest_ops.wizard_candidates(str(frame))
    assert candidates == [{"part_number": "W25Q64JVSIQ", "manufacturer": "Winbond", "confidence": 0.8, "bbox": [1, 2, 3, 4]}]


def test_wizard_candidates_empty_on_vision_failure(tmp_path, monkeypatch):
    frame = _frame(tmp_path)
    monkeypatch.setattr(vision_ops, "identify_components", lambda path, model=None: {
        "ok": False, "model": "x", "error": "boom", "components": [],
    })
    assert manifest_ops.wizard_candidates(str(frame)) == []
