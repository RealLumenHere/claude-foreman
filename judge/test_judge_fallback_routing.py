"""wq#3783: hathor-aware routing must not fail the judge gate closed just
because the pinned primary (hathor-llama8b) is asleep/unreachable -- that now
requires BOTH the primary and the configured fallback to be unreachable."""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import judge_common as jc

PIN = json.load(open(jc.IDENTITY_FILE))["hathor-llama8b"]["blob_sha256"]


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _serve(monkeypatch, exc=None):
    def fake(req, timeout=None):
        if exc:
            raise exc
        return _Resp(b"{}")
    monkeypatch.setattr(jc.urllib.request, "urlopen", fake)


def test_reachable_primary_has_no_note(monkeypatch, tmp_path):
    _serve(monkeypatch)
    pin = tmp_path / "pin"
    pin.write_text("hathor-llama8b\n")
    model, source, note = jc.resolve_judge_model(str(pin))
    assert (model, source, note) == ("hathor-llama8b", f"pin:{pin}", "")


def test_unreachable_primary_routes_to_fallback(monkeypatch, tmp_path):
    _serve(monkeypatch, exc=OSError("no route to host"))
    pin = tmp_path / "pin"
    pin.write_text("hathor-llama8b\n")
    fallback = tmp_path / "fallback"
    fallback.write_text("nephthys-qwen14b\n")
    model, source, note = jc.resolve_judge_model(str(pin), fallback_pin_file=str(fallback))
    assert model == "nephthys-qwen14b"
    assert source == f"fallback:{fallback}"
    assert "unreachable" in note and "nephthys-qwen14b" in note


def test_unreachable_primary_with_no_fallback_configured_stays_on_primary(monkeypatch, tmp_path):
    """No fallback pin at all must still surface the primary (so the caller's
    existing call_judge()/fail_closed() path runs unchanged) -- this is the
    pre-wq#3783 behavior, preserved when nothing else is configured."""
    _serve(monkeypatch, exc=OSError("no route to host"))
    pin = tmp_path / "pin"
    pin.write_text("hathor-llama8b\n")
    missing_fallback = tmp_path / "no-such-fallback-file"
    model, source, note = jc.resolve_judge_model(str(pin), fallback_pin_file=str(missing_fallback))
    assert model == "hathor-llama8b"
    assert source == f"pin:{pin}"
    assert "no fallback configured" in note


def test_env_override_takes_no_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("JUDGE_MODEL", "some-bench-model")
    pin = tmp_path / "pin"
    pin.write_text("hathor-llama8b\n")
    model, source, note = jc.resolve_judge_model(str(pin))
    assert (model, source, note) == ("some-bench-model", "env:JUDGE_MODEL", "")


def test_no_pin_and_no_env_returns_none(tmp_path):
    missing = tmp_path / "no-such-pin"
    model, source, note = jc.resolve_judge_model(str(missing))
    assert model is None and source is None
    assert "no model configured" in note
