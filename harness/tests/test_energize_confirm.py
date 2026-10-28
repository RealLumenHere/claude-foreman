"""wq#4747 — energize_confirm.py: operator-issued confirm tokens for hardware MCP
tools (hal-spec.md §9.1/§9.2a). The property under test is that a headless/agent
session cannot mint its own confirm token (issue_token fails outside a real TTY),
and that a minted token is capability-scoped, time-limited and single-use."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import energize_confirm as ec


@pytest.fixture(autouse=True)
def isolated_token_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ec, "TOKEN_DIR", str(tmp_path / ".energize-confirm"))


def test_issue_token_refuses_outside_a_real_tty():
    with pytest.raises(ec.EnergizeConfirmError, match="interactive terminal"):
        ec.issue_token("openocd.write_target_memory", _isatty=False)


def test_issue_token_succeeds_from_a_real_tty():
    token = ec.issue_token("openocd.write_target_memory", _isatty=True)
    assert token and isinstance(token, str)


def test_verify_token_accepts_a_matching_capability():
    token = ec.issue_token("flashrom.write_flash", _isatty=True)
    assert ec.verify_token(token, "flashrom.write_flash") is True


def test_verify_token_rejects_a_different_capability():
    token = ec.issue_token("flashrom.write_flash", _isatty=True)
    assert ec.verify_token(token, "flashrom.erase_chip") is False


def test_verify_token_rejects_empty_or_unknown_token():
    assert ec.verify_token("", "flashrom.write_flash") is False
    assert ec.verify_token("not-a-real-token", "flashrom.write_flash") is False


def test_verify_token_is_single_use():
    token = ec.issue_token("flashrom.erase_chip", _isatty=True)
    assert ec.verify_token(token, "flashrom.erase_chip") is True
    assert ec.verify_token(token, "flashrom.erase_chip") is False  # consumed


def test_verify_token_rejects_an_expired_token():
    token = ec.issue_token("flashrom.erase_chip", ttl_seconds=-1, _isatty=True)
    assert ec.verify_token(token, "flashrom.erase_chip") is False


def test_cli_issue_refuses_outside_a_real_tty(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    rc = ec._main(["issue", "openocd.write_target_memory"])
    assert rc == 1


def test_cli_verify_round_trip(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    token = ec.issue_token("serial.write", _isatty=True)
    assert ec._main(["verify", "serial.write", token]) == 0
    assert ec._main(["verify", "serial.write", "bogus"]) == 1
