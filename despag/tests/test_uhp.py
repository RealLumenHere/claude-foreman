"""wq#1805: pytest unit tests for the error envelope, UHP-Version negotiation,
and `available` computation. The conformance suite (despag/reports/) covers the
black-box HTTP surface; these cover the pieces it can't reach directly."""
from __future__ import annotations

import os
import time

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


def test_streaming_responses_not_implemented(client):
    # wq#1806 wires POST /v1/responses for stream:false (blocking + background) only —
    # SSE is the next ticket. stream:true must still answer 501, never attempt a turn.
    c, app_module = client
    r = c.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {app_module.API_TOKEN}"},
        json={"input": "hi", "stream": True},
    )
    assert r.status_code == 501
    assert r.json()["error"]["code"] == "lumen_not_implemented"


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


# --- wq#1857: admission (wq#1807) around the real runner (wq#1806) ------------------
# These drive create_response all the way through TurnRegistry.start into a live child
# process — fake_backend.py standing in for `claude -p`, so no Claude subscription is
# needed — which is the only way to prove the Idempotency-Key and the session lease are
# actually on the runner's path and not on a 501 stub's.

@pytest.fixture()
def runner_client(tmp_path, monkeypatch):
    """`(make_client, mode_holder)`. The caller enters the TestClient as a context
    manager so ONE event loop spans the whole test — `background:true` finishes its
    turn in an asyncio task, which a per-request portal would tear down."""
    import importlib
    import sys
    from pathlib import Path

    monkeypatch.setenv("DESPAG_API_TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.setenv("DESPAG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DESPAG_SESSIONS_ROOT", str(tmp_path / "sessions"))

    import despag.app as app_module

    importlib.reload(app_module)
    fake = str(Path(__file__).resolve().parent / "fake_backend.py")
    mode: list[str] = ["--mode", "quick"]

    monkeypatch.setattr(app_module, "compute_available", lambda h: h.get("enabled", True))
    monkeypatch.setattr(app_module.claude_max, "build_turn",
                        lambda **kw: ([sys.executable, fake, *mode], dict(os.environ)))
    return app_module, mode


def _auth(app_module):
    return {"Authorization": f"Bearer {app_module.API_TOKEN}"}


def _poll_until_terminal(c, app_module, response_id, timeout_s=20):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        body = c.get(f"/v1/responses/{response_id}", headers=_auth(app_module)).json()
        if body["status"] != "in_progress":
            return body
        time.sleep(0.05)
    raise AssertionError(f"{response_id} never left in_progress")


def test_blocking_turn_runs_and_returns_output(runner_client):
    app_module, _ = runner_client
    with TestClient(app_module.app) as c:
        r = c.post("/v1/responses", headers=_auth(app_module), json={"input": "hi"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["output"][0]["content"][0]["text"] == "ok"


def test_idempotency_key_replays_the_winner_instead_of_a_second_turn(runner_client):
    """wq#1853's core finding: wq#1806's create_response had no path to _idem_admit, so
    a retry spawned a SECOND `claude -p`. The retry must now return the first
    response verbatim, and RESPONSES must still hold exactly one record."""
    app_module, _ = runner_client
    headers = {**_auth(app_module), "Idempotency-Key": "retry-me"}
    with TestClient(app_module.app) as c:
        r1 = c.post("/v1/responses", headers=headers, json={"input": "hi"})
        r2 = c.post("/v1/responses", headers=headers, json={"input": "hi"})
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    assert r2.json()["id"] == r1.json()["id"]
    assert r2.json() == r1.json()
    assert len(app_module.RESPONSES) == 1


def test_idempotency_key_still_running_is_409(runner_client):
    app_module, mode = runner_client
    mode[:] = ["--mode", "steady", "--count", "400", "--interval", "0.05"]
    headers = {**_auth(app_module), "Idempotency-Key": "in-flight"}
    with TestClient(app_module.app) as c:
        r1 = c.post("/v1/responses", headers=headers, json={"input": "hi", "background": True})
        assert r1.status_code == 200, r1.text
        r2 = c.post("/v1/responses", headers=headers, json={"input": "hi", "background": True})
        assert r2.status_code == 409, r2.text
        assert r2.json()["error"]["code"] == "lumen_still_running"
        c.post(f"/v1/responses/{r1.json()['id']}/cancel", headers=_auth(app_module))


def test_second_turn_of_a_running_session_is_409_session_busy(runner_client):
    """The lease fences the RESOLVED hsess id for as long as the turn actually runs —
    a background turn's lease must not be released when the request returns."""
    app_module, mode = runner_client
    mode[:] = ["--mode", "steady", "--count", "400", "--interval", "0.05"]
    with TestClient(app_module.app) as c:
        r1 = c.post("/v1/responses", headers=_auth(app_module), json={"input": "hi", "background": True})
        assert r1.status_code == 200, r1.text
        sid = r1.json()["metadata"]["session_id"]
        r2 = c.post("/v1/responses", headers=_auth(app_module),
                    json={"input": "again", "previous_response_id": r1.json()["id"]})
        assert r2.status_code == 409, r2.text
        assert r2.json()["error"]["code"] == "session_busy"
        assert sid in r2.json()["error"]["message"]
        c.post(f"/v1/responses/{r1.json()['id']}/cancel", headers=_auth(app_module))


def test_two_consecutive_turns_of_one_session_are_both_admitted(runner_client):
    """wq#1851: the lease is released when the turn ENDS, so ordinary multi-turn
    conversation keeps working instead of 409ing for SESSION_LEASE_TTL_S."""
    app_module, _ = runner_client
    with TestClient(app_module.app) as c:
        r1 = c.post("/v1/responses", headers=_auth(app_module), json={"input": "hi"})
        assert r1.status_code == 200, r1.text
        r2 = c.post("/v1/responses", headers=_auth(app_module),
                    json={"input": "again", "previous_response_id": r1.json()["id"]})
    assert r2.status_code == 200, r2.text
    assert r2.json()["metadata"]["session_id"] == r1.json()["metadata"]["session_id"]


def test_background_turn_releases_its_lease_when_it_finishes(runner_client):
    app_module, _ = runner_client
    with TestClient(app_module.app) as c:
        r1 = c.post("/v1/responses", headers=_auth(app_module), json={"input": "hi", "background": True})
        assert r1.json()["status"] == "in_progress", r1.text
        _poll_until_terminal(c, app_module, r1.json()["id"])
        r2 = c.post("/v1/responses", headers=_auth(app_module),
                    json={"input": "again", "previous_response_id": r1.json()["id"]})
    assert r2.status_code == 200, r2.text


def test_unknown_explicit_session_id_is_session_not_found(runner_client):
    """An hsess id names a real workspace now; a client cannot conjure one that the
    lease has never seen."""
    app_module, _ = runner_client
    with TestClient(app_module.app) as c:
        r = c.post("/v1/responses", headers=_auth(app_module),
                   json={"input": "hi", "metadata": {"session_id": "hsess_never-existed"}})
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "session_not_found"


def test_failed_admission_frees_the_idempotency_key(runner_client):
    """A request rejected AFTER reserving its key (here: an unknown session) must
    release the reservation — the retry that fixes the request must not 409 forever."""
    import asyncio

    app_module, _ = runner_client
    headers = {**_auth(app_module), "Idempotency-Key": "released-again"}
    with TestClient(app_module.app) as c:
        r1 = c.post("/v1/responses", headers=headers,
                    json={"input": "hi", "metadata": {"session_id": "hsess_never-existed"}})
        assert r1.status_code == 404, r1.text
        assert asyncio.run(app_module.CONTROL.idem_get("released-again")) is None
        r2 = c.post("/v1/responses", headers=headers, json={"input": "hi"})
    assert r2.status_code == 200, r2.text


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
