"""wq#1805: pytest unit tests for the error envelope, UHP-Version negotiation,
and `available` computation. The conformance suite (despag/reports/) covers the
black-box HTTP surface; these cover the pieces it can't reach directly."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DESPAG_API_TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.setenv("DESPAG_DATA_DIR", str(tmp_path / "data"))
    import importlib

    import despag.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app), app_module


def test_discovery_unauthenticated(client):
    c, _ = client
    r = c.get("/v1/uhp")
    assert r.status_code == 200
    body = r.json()
    assert body["capabilities"]["streaming"] is True
    assert body["capabilities"]["files_input"] is False
    assert body["capabilities"]["session_sharing"] is False
    assert body["versions"] == ["2026-08-11"]


def test_uhp_version_header_on_every_response(client):
    c, _ = client
    r = c.get("/v1/uhp")
    assert r.headers["uhp-version"] == "2026-08-11"


def test_unsupported_version_rejected(client):
    c, _ = client
    r = c.get("/v1/uhp", headers={"UHP-Version": "1999-01-01"})
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "unsupported_protocol_version"
    assert err["detail"]["supported"] == ["2026-08-11"]


def test_supported_version_honoured(client):
    c, _ = client
    r = c.get("/v1/uhp", headers={"UHP-Version": "2026-08-11"})
    assert r.status_code == 200


def test_missing_credential_rejected(client):
    c, _ = client
    r = c.get("/v1/harnesses")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "missing_credential"


def test_invalid_credential_rejected(client):
    c, _ = client
    r = c.get("/v1/harnesses", headers={"Authorization": "Bearer not-the-token"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credential"


def test_valid_credential_accepted(client):
    c, app_module = client
    r = c.get("/v1/harnesses", headers={"Authorization": f"Bearer {app_module.API_TOKEN}"})
    assert r.status_code == 200
    assert {h["id"] for h in r.json()["harnesses"]} == {
        "chrn_claude-max", "chrn_litellm-mechanical", "chrn_claude-local-rung",
    }


def test_unknown_harness_is_404_not_403(client):
    c, app_module = client
    r = c.get("/v1/harnesses/chrn_does-not-exist", headers={"Authorization": f"Bearer {app_module.API_TOKEN}"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "harness_not_found"


def test_error_envelope_shape(client):
    c, _ = client
    r = c.get("/v1/harnesses")
    body = r.json()
    assert set(body.keys()) == {"error"}
    err = body["error"]
    assert set(err.keys()) == {"type", "code", "message", "param", "detail"}


def _patch_fake_backend(app_module, monkeypatch, *, mode="quick"):
    """wq#1809: swap the real `claude -p` backend for fake_backend.py (same fixture
    despag/tests/test_runner.py uses) so POST /v1/responses can be exercised end to
    end — real execution, real translator, real SSE — without spawning the CLI or
    spending window budget. Returns the list of kwargs each call was made with."""
    import sys
    from pathlib import Path

    fake_backend = str(Path(__file__).resolve().parent / "fake_backend.py")
    calls: list[dict] = []

    def fake_build_turn(**kwargs):
        calls.append(kwargs)
        return [sys.executable, fake_backend, "--mode", mode], dict(os.environ)

    monkeypatch.setattr(app_module.claude_max, "build_turn", fake_build_turn)
    # The sandbox running this test has no logged-in `claude` CLI; availability is
    # not what these tests are about (see test_disabled_harness_never_available).
    monkeypatch.setattr(app_module, "compute_available", lambda harness: True)
    return calls


def _auth(app_module) -> dict:
    return {"Authorization": f"Bearer {app_module.API_TOKEN}"}


def _parse_sse(body: str) -> list[dict]:
    import json

    events = []
    for block in body.split("\n\n"):
        block = block.strip()
        if block.startswith("data:"):
            events.append(json.loads(block[len("data:"):].strip()))
    return events


def test_responses_blocking_runs_the_backend_and_returns_completed(client, monkeypatch):
    c, app_module = client
    _patch_fake_backend(app_module, monkeypatch, mode="quick")
    r = c.post("/v1/responses", headers=_auth(app_module), json={"input": "hi"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["output"][0]["content"][0]["text"] == "ok"
    assert body["metadata"]["session_id"]


def test_responses_stream_emits_gapless_events_ending_in_one_terminal(client, monkeypatch):
    c, app_module = client
    _patch_fake_backend(app_module, monkeypatch, mode="quick")
    r = c.post("/v1/responses", headers=_auth(app_module), json={"input": "hi", "stream": True})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache"
    events = _parse_sse(r.text)
    assert events[0]["type"] == "response.created"
    assert [e["sequence_number"] for e in events] == list(range(len(events)))
    terminals = {"response.completed", "response.incomplete", "response.failed"}
    assert [e["type"] for e in events if e["type"] in terminals] == ["response.completed"]
    assert events[-1]["response"]["output"][0]["content"][0]["text"] == "ok"


def test_responses_stream_and_blocking_agree_on_output_shape(client, monkeypatch):
    # streaming.md §6 / S-08: the two paths must produce the same result for the
    # same input — checked here at the HTTP level, not just inside the translator.
    c, app_module = client
    _patch_fake_backend(app_module, monkeypatch, mode="quick")
    blocking = c.post("/v1/responses", headers=_auth(app_module), json={"input": "hi", "stream": False}).json()
    stream_events = _parse_sse(
        c.post("/v1/responses", headers=_auth(app_module), json={"input": "hi", "stream": True}).text)
    streamed = stream_events[-1]["response"]
    assert blocking["status"] == streamed["status"] == "completed"
    strip_id = lambda output: [{k: v for k, v in item.items() if k != "id"} for item in output]
    assert strip_id(blocking["output"]) == strip_id(streamed["output"])


def test_responses_idempotency_key_replays_settled_output_without_rerunning(client, monkeypatch):
    """wq#1809: now that execution is real, a retried Idempotency-Key must replay the
    FIRST run's actual output rather than release-and-rerun (P1-1's stub-era
    behaviour, now obsolete — despag/app.py module docstring)."""
    c, app_module = client
    calls = _patch_fake_backend(app_module, monkeypatch, mode="quick")
    headers = {**_auth(app_module), "Idempotency-Key": "abc123"}
    r1 = c.post("/v1/responses", headers=headers, json={"input": "hi"})
    assert r1.status_code == 200
    r2 = c.post("/v1/responses", headers=headers, json={"input": "hi"})
    assert r2.status_code == 200
    assert r2.json() == r1.json()
    assert len(calls) == 1, "a replayed Idempotency-Key must not run the backend a second time"


def test_responses_session_busy_when_continuing_a_session_under_another_owners_lease(client, monkeypatch):
    """wq#1807/wq#1809: a session is implicit and extended via `previous_response_id`
    (metadata.session_id is not a session selector) — so to exercise the 409 path,
    continue a real response's session and pre-seed a conflicting lease on it."""
    import asyncio

    c, app_module = client
    _patch_fake_backend(app_module, monkeypatch, mode="quick")
    first = c.post("/v1/responses", headers=_auth(app_module), json={"input": "hi"}).json()
    sid = first["metadata"]["session_id"]
    asyncio.run(app_module.CONTROL.lease_admit(sid, "some-other-inflight-request", ttl_s=60))
    r = c.post("/v1/responses", headers=_auth(app_module),
                json={"input": "again", "previous_response_id": first["id"]})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "session_busy"


def test_responses_a_fresh_session_is_never_session_busy(client, monkeypatch):
    import asyncio

    c, app_module = client
    _patch_fake_backend(app_module, monkeypatch, mode="quick")
    asyncio.run(app_module.CONTROL.lease_admit("hsess_unrelated", "some-other-inflight-request", ttl_s=60))
    r = c.post("/v1/responses", headers=_auth(app_module), json={"input": "hi"})
    assert r.status_code == 200  # a brand new session never conflicts with an unrelated one


def test_unknown_harness_id_on_create_response(client):
    c, app_module = client
    r = c.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {app_module.API_TOKEN}"},
        json={"input": "hi", "metadata": {"harness_id": "chrn_does-not-exist"}},
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "harness_not_found"


def test_disabled_harness_rejected_on_create_response(client):
    c, app_module = client
    r = c.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {app_module.API_TOKEN}"},
        json={"input": "hi", "metadata": {"harness_id": "chrn_claude-local-rung"}},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "model_unavailable"


def test_disabled_harness_never_available(client):
    _, app_module = client
    h = app_module.HARNESS_BY_ID["chrn_claude-local-rung"]
    assert h["enabled"] is False
    assert app_module.compute_available(h) is False


def test_vendor_error_code_must_be_lumen_prefixed():
    from despag.uhp.errors import error_response

    with pytest.raises(ValueError):
        error_response("not_prefixed", "boom")


def test_closed_code_table_status_codes():
    from despag.uhp.errors import error_response

    r = error_response("harness_not_found", "no harness")
    assert r.status_code == 404
    r = error_response("session_busy", "busy")
    assert r.status_code == 409
    r = error_response("rate_limited", "slow down")
    assert r.status_code == 429
