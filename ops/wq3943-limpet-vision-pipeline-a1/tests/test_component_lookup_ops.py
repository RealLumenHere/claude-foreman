"""Tests for ops/wq3943-limpet-vision-pipeline-a1/component_lookup_ops.py.

No real FlashDetector binary, Part-DB instance, or network is exercised:
shutil.which / subprocess.run / urllib.request.urlopen are monkeypatched.
"""
import io
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import component_lookup_ops as ops  # noqa: E402


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("marking,manufacturer", [
    ("W25Q64JVSIQ", "Winbond"),
    ("25Q64JVSIQ", "Winbond"),
    ("GD25Q128CSIG", "GigaDevice"),
    ("MX25L12835F", "Macronix"),
])
def test_flash_marking_decode_known_prefixes(marking, manufacturer, monkeypatch):
    monkeypatch.setattr(ops, "FLASHDETECTOR_BIN", "")
    hit = ops.flash_marking_decode(marking)
    assert hit is not None
    assert hit["manufacturer"] == manufacturer
    assert hit["mpn"] == marking


def test_flash_marking_decode_returns_none_for_unknown(monkeypatch):
    monkeypatch.setattr(ops, "FLASHDETECTOR_BIN", "")
    assert ops.flash_marking_decode("TOTALLY UNKNOWN 123") is None


def test_flash_marking_decode_prefers_external_flashdetector(monkeypatch):
    monkeypatch.setattr(ops, "FLASHDETECTOR_BIN", "/usr/bin/flashdetector")

    def fake_run(args, **kwargs):
        assert args == ["/usr/bin/flashdetector", "W25Q64JVSIQ"]
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"manufacturer": "Winbond-external"}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    hit = ops.flash_marking_decode("W25Q64JVSIQ")
    assert hit == {"manufacturer": "Winbond-external"}


def test_flash_marking_decode_external_failure_falls_back_to_local(monkeypatch):
    monkeypatch.setattr(ops, "FLASHDETECTOR_BIN", "/usr/bin/flashdetector")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout=""))
    hit = ops.flash_marking_decode("W25Q64JVSIQ")
    assert hit["resolver"] == "flashdetector-style"


def test_partdb_search_unconfigured_degrades():
    result = ops.partdb_search("W25Q64", base_url="")
    assert result["ok"] is False
    assert "not configured" in result["error"]


def test_partdb_search_returns_results_list(monkeypatch):
    payload = {"results": [{"mpn": "W25Q64JVSIQ", "manufacturer": "Winbond"}]}
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: FakeResponse(json.dumps(payload).encode()))
    result = ops.partdb_search("W25Q64", base_url="http://partdb.local")
    assert result["ok"] is True
    assert result["results"] == payload["results"]


def test_partdb_search_handles_http_error(monkeypatch):
    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, io.BytesIO(b""))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = ops.partdb_search("W25Q64", base_url="http://partdb.local")
    assert result["ok"] is False
    assert "HTTP 500" in result["error"]


def test_partdb_search_handles_unreachable_host(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: (_ for _ in ()).throw(urllib.error.URLError("refused")))
    result = ops.partdb_search("W25Q64", base_url="http://partdb.local")
    assert result["ok"] is False
    assert "unreachable" in result["error"]


def test_lookup_part_prefers_flash_decode_over_partdb(monkeypatch):
    monkeypatch.setattr(ops, "FLASHDETECTOR_BIN", "")

    def fail_if_called(*a, **k):
        raise AssertionError("must not fall through to Part-DB when flash decode hits")

    monkeypatch.setattr(ops, "partdb_search", fail_if_called)
    result = ops.lookup_part("W25Q64JVSIQ")
    assert result["resolved"]["manufacturer"] == "Winbond"
    assert result["needs_review"] is False


def test_lookup_part_single_partdb_hit_resolves(monkeypatch):
    monkeypatch.setattr(ops, "FLASHDETECTOR_BIN", "")
    monkeypatch.setattr(ops, "partdb_search", lambda marking, **k: {
        "ok": True, "results": [{"mpn": "ATMEGA328P", "manufacturer": "Microchip"}], "error": None,
    })
    result = ops.lookup_part("ATMEGA328P-AU")
    assert result["resolved"]["mpn"] == "ATMEGA328P"
    assert result["needs_review"] is False


def test_lookup_part_multiple_partdb_hits_needs_review(monkeypatch):
    monkeypatch.setattr(ops, "FLASHDETECTOR_BIN", "")
    monkeypatch.setattr(ops, "partdb_search", lambda marking, **k: {
        "ok": True, "results": [{"mpn": "A"}, {"mpn": "B"}], "error": None,
    })
    result = ops.lookup_part("AMBIGUOUS")
    assert result["resolved"] is None
    assert result["needs_review"] is True
    assert len(result["candidates"]) == 2


def test_lookup_part_no_hits_is_honest_not_a_guess(monkeypatch):
    monkeypatch.setattr(ops, "FLASHDETECTOR_BIN", "")
    monkeypatch.setattr(ops, "partdb_search", lambda marking, **k: {
        "ok": False, "results": [], "error": "Part-DB not configured (set LIMPET_PARTDB_BASE_URL)",
    })
    result = ops.lookup_part("??2")
    assert result["resolved"] is None
    assert result["candidates"] == []
    assert result["needs_review"] is True
    assert "not configured" in result["resolver_error"]
