#!/usr/bin/env python3
"""wq#4747 — energize-confirm gate on server.py's write_target_memory/flash_firmware.

openocd_client.py (tested in test_openocd_client.py) never sees these calls when the
token is missing/invalid: the gate in server.py raises before _client is touched, which
is exactly the property that matters here (a model-supplied bool used to be the only
check; now a real client method call never happens without a verified operator token).
_client.write_memory/flash_write are monkeypatched so this never needs a live OpenOCD
process -- that machinery is test_openocd_client.py's job.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server as srv  # noqa: E402


@pytest.fixture
def no_client_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(srv._client, "write_memory", lambda *a, **k: calls.append(("write_memory", a, k)) or "ok")
    monkeypatch.setattr(srv._client, "flash_write", lambda *a, **k: calls.append(("flash_write", a, k)) or "ok")
    return calls


def test_write_target_memory_refuses_without_confirm_token(no_client_calls):
    with pytest.raises(srv.ConfirmRequiredError, match="confirm_token"):
        srv.write_target_memory(0x2000, [1, 2, 3])
    assert no_client_calls == []


def test_write_target_memory_refuses_invalid_confirm_token(no_client_calls, monkeypatch):
    monkeypatch.setattr(srv, "_verify_confirm_token", lambda capability, token: False)
    with pytest.raises(srv.ConfirmRequiredError, match="invalid or expired"):
        srv.write_target_memory(0x2000, [1, 2, 3], confirm_token="bogus")
    assert no_client_calls == []


def test_write_target_memory_runs_with_valid_confirm_token(no_client_calls, monkeypatch):
    monkeypatch.setattr(srv, "_verify_confirm_token", lambda capability, token: True)
    result = srv.write_target_memory(0x2000, [1, 2, 3], confirm_token="tok")
    assert result == "ok"
    assert no_client_calls[0][0] == "write_memory"


def test_flash_firmware_refuses_without_confirm_token(no_client_calls):
    with pytest.raises(srv.ConfirmRequiredError, match="confirm_token"):
        srv.flash_firmware("/tmp/image.bin", 0x8000000)
    assert no_client_calls == []


def test_flash_firmware_runs_with_valid_confirm_token(no_client_calls, monkeypatch):
    monkeypatch.setattr(srv, "_verify_confirm_token", lambda capability, token: True)
    result = srv.flash_firmware("/tmp/image.bin", 0x8000000, confirm_token="tok")
    assert result == "ok"
    assert no_client_calls[0][0] == "flash_write"


def test_read_target_memory_is_unaffected_by_the_gate(monkeypatch):
    monkeypatch.setattr(srv._client, "read_memory", lambda *a, **k: [0xDEADBEEF])
    assert srv.read_target_memory(0x2000) == [0xDEADBEEF]
