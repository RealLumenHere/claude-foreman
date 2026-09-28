"""wq#2904: verify_model_registered must fail loud on a model_group missing
from litellm_config.yaml -- the class of bug that let minerva-bench-2 burn
169 silent calls on wq2821-judge-llama8b over 5 days before anyone noticed."""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import judge_common as jc

MODELS_BODY = json.dumps({"data": [{"id": "hathor-llama8b"}, {"id": "qwen36-local"}]}).encode()


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _serve(monkeypatch, body=MODELS_BODY, exc=None):
    def fake(req, timeout=None):
        if exc:
            raise exc
        return _Resp(body)
    monkeypatch.setattr(jc.urllib.request, "urlopen", fake)
    monkeypatch.setattr(jc, "litellm_key", lambda: "sk-test")


def test_registered_model_passes(monkeypatch):
    _serve(monkeypatch)
    assert jc.verify_model_registered("hathor-llama8b") is None


def test_missing_model_fails_loud(monkeypatch):
    _serve(monkeypatch)
    err = jc.verify_model_registered("wq2821-judge-llama8b")
    assert err and "not registered" in err and "wq2821-judge-llama8b" in err


def test_gateway_unreachable_fails_loud(monkeypatch):
    _serve(monkeypatch, exc=OSError("no route"))
    err = jc.verify_model_registered("hathor-llama8b")
    assert err and "could not reach gateway" in err


def test_no_key_fails_loud(monkeypatch):
    def boom():
        raise RuntimeError("no LiteLLM key found")
    monkeypatch.setattr(jc, "litellm_key", boom)
    err = jc.verify_model_registered("hathor-llama8b")
    assert err and "cannot load LiteLLM key" in err
