#!/usr/bin/env python3
"""Tests for qemu_client.py (wq#4416).

End-to-end tests spawn tests/fake_qemu.py as a real subprocess speaking the
real QMP wire protocol over a real AF_UNIX socket (in a fresh tmp_path per
test, so parallel runs don't collide) — boot()/quit() and every tool-backing
method are exercised against real process + socket machinery, not mocks.
Only the *responses* are canned, by fake_qemu.py's dispatch table.

Per wq#2630 (an acceptance probe must be able to fail): the not-booted,
double-boot, missing-ISO, invalid-resource, and unmapped-character cases are
probed explicitly so a regression that silently accepts bad input fails
loudly, not just the happy path that was designed to pass. wait_serial's
timeout branch is probed with a pattern that is never written, not just the
pattern that is, so that branch can fail too.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qemu_client import QMPClient, QMPError  # noqa: E402

FAKE_QEMU = str(Path(__file__).resolve().parent / "fake_qemu.py")


@pytest.fixture
def iso(tmp_path):
    path = tmp_path / "limpet.iso"
    path.write_bytes(b"\x00" * 16)
    return path


@pytest.fixture
def client():
    c = QMPClient(executable=FAKE_QEMU, connect_timeout=5.0, command_timeout=5.0)
    yield c
    if c.connected or c._proc is not None:
        c.quit()


def test_boot_quit_lifecycle(client, iso, tmp_path):
    info = client.boot(str(iso), str(tmp_path / "run"))
    assert client.connected
    assert info["pid"] > 0
    assert info["vnc_port"] == 5901
    assert Path(info["qmp_sock"]).exists()
    out = client.quit()
    assert "stopped" in out
    assert not client.connected


def test_boot_rejects_missing_iso(client, tmp_path):
    with pytest.raises(FileNotFoundError):
        client.boot(str(tmp_path / "nope.iso"), str(tmp_path / "run"))


def test_boot_rejects_invalid_memory(client, iso, tmp_path):
    with pytest.raises(ValueError):
        client.boot(str(iso), str(tmp_path / "run"), memory_mb=0)


def test_boot_rejects_invalid_smp(client, iso, tmp_path):
    with pytest.raises(ValueError):
        client.boot(str(iso), str(tmp_path / "run"), smp=0)


def test_double_boot_rejected(client, iso, tmp_path):
    client.boot(str(iso), str(tmp_path / "run"))
    with pytest.raises(QMPError):
        client.boot(str(iso), str(tmp_path / "run2"))


def test_read_serial_requires_boot():
    c = QMPClient()
    with pytest.raises(QMPError):
        c.read_serial()


def test_screenshot_requires_boot():
    c = QMPClient()
    with pytest.raises(QMPError):
        c.screenshot("/tmp/does-not-matter.png")


def test_screenshot_writes_real_file(client, iso, tmp_path):
    client.boot(str(iso), str(tmp_path / "run"))
    out_png = tmp_path / "frame.png"
    path = client.screenshot(str(out_png))
    assert path == str(out_png)
    assert out_png.read_bytes().startswith(b"\x89PNG")


def test_read_serial_tails_and_does_not_reread(client, iso, tmp_path):
    client.boot(str(iso), str(tmp_path / "run"))
    first = client.read_serial()
    assert "SeaBIOS" in first
    assert "limpet login:" in first
    second = client.read_serial()
    assert second == ""


def test_wait_serial_matches_existing_content(client, iso, tmp_path):
    client.boot(str(iso), str(tmp_path / "run"))
    result = client.wait_serial("login:", timeout_s=5.0, poll_interval_s=0.05)
    assert result["matched"] is True
    assert "login:" in result["text"]


def test_wait_serial_times_out_on_absent_pattern(client, iso, tmp_path):
    client.boot(str(iso), str(tmp_path / "run"))
    result = client.wait_serial("Kernel panic", timeout_s=0.3, poll_interval_s=0.05)
    assert result["matched"] is False
    assert result["elapsed_s"] >= 0.3


def test_wait_serial_rejects_negative_timeout(client, iso, tmp_path):
    client.boot(str(iso), str(tmp_path / "run"))
    with pytest.raises(ValueError):
        client.wait_serial("login:", timeout_s=-1)


def test_type_text_sends_expected_chords(client, iso, tmp_path):
    run_dir = tmp_path / "run"
    client.boot(str(iso), str(run_dir))
    client.type_text("Hi!", key_delay_s=0)
    keys_log = run_dir / "keys.jsonl"
    calls = [json.loads(line) for line in keys_log.read_text().splitlines()]
    assert calls == [
        {"keys": [{"type": "qcode", "data": "shift"}, {"type": "qcode", "data": "h"}]},
        {"keys": [{"type": "qcode", "data": "i"}]},
        {"keys": [{"type": "qcode", "data": "shift"}, {"type": "qcode", "data": "1"}]},
    ]


def test_type_text_rejects_unmapped_character(client, iso, tmp_path):
    client.boot(str(iso), str(tmp_path / "run"))
    with pytest.raises(ValueError):
        client.type_text("é")
