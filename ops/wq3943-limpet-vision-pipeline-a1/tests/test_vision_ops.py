"""Tests for ops/wq3943-limpet-vision-pipeline-a1/vision_ops.py.

No real Ollama server is contacted: urllib.request.urlopen is monkeypatched
to return a canned response built in-memory, same shape the real
/api/generate call would return.
"""
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import vision_ops as ops  # noqa: E402


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_identify_components_missing_image_degrades_without_network(tmp_path, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("must not contact ollama for an unreadable image")

    monkeypatch.setattr(urllib.request, "urlopen", fail_if_called)
    result = ops.identify_components(str(tmp_path / "nope.jpg"))
    assert result["ok"] is False
    assert "cannot read" in result["error"]


def test_identify_components_parses_structured_response(tmp_path, monkeypatch):
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"\xff\xd8fake-jpeg-bytes")

    body = {
        "response": json.dumps({
            "components": [
                {"bbox": [1, 2, 3, 4], "package": "SOIC-8", "markings": ["W25Q64JVSIQ"], "confidence": 0.9}
            ]
        })
    }
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: FakeResponse(json.dumps(body).encode()))

    result = ops.identify_components(str(image))
    assert result["ok"] is True
    assert result["components"][0]["markings"] == ["W25Q64JVSIQ"]
    assert result["model"] == ops.DEFAULT_MODEL


def test_identify_components_sends_base64_image_and_schema(tmp_path, monkeypatch):
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"\xff\xd8fake-jpeg-bytes")
    captured = {}

    def fake_urlopen(req, timeout):
        captured["body"] = json.loads(req.data.decode())
        return FakeResponse(json.dumps({"response": json.dumps({"components": []})}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    ops.identify_components(str(image))
    assert captured["body"]["format"] == ops.RESPONSE_SCHEMA
    assert captured["body"]["images"]  # non-empty base64 list
    assert captured["body"]["model"] == ops.DEFAULT_MODEL


def test_identify_components_handles_unreachable_ollama(tmp_path, monkeypatch):
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"\xff\xd8fake-jpeg-bytes")

    def fake_urlopen(req, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = ops.identify_components(str(image))
    assert result["ok"] is False
    assert "unreachable" in result["error"]


def test_identify_components_handles_model_not_pulled(tmp_path, monkeypatch):
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"\xff\xd8fake-jpeg-bytes")

    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "model not found", {}, io.BytesIO(b'{"error":"model not found"}'))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = ops.identify_components(str(image))
    assert result["ok"] is False
    assert "HTTP 404" in result["error"]


def test_identify_components_rejects_non_json_response(tmp_path, monkeypatch):
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"\xff\xd8fake-jpeg-bytes")
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout: FakeResponse(json.dumps({"response": "not json at all"}).encode()),
    )
    result = ops.identify_components(str(image))
    assert result["ok"] is False
    assert "valid JSON" in result["error"]


def test_identify_components_rejects_missing_components_key(tmp_path, monkeypatch):
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"\xff\xd8fake-jpeg-bytes")
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout: FakeResponse(json.dumps({"response": json.dumps({"not_components": []})}).encode()),
    )
    result = ops.identify_components(str(image))
    assert result["ok"] is False
    assert "components" in result["error"]
