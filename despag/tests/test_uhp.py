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


def test_responses_not_implemented(client):
    c, app_module = client
    r = c.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {app_module.API_TOKEN}"},
        json={"input": "hi"},
    )
    assert r.status_code == 501
    assert r.json()["error"]["code"] == "lumen_not_implemented"


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
