#!/usr/bin/env python3
"""Tests for openocd_client.py (wq#3881).

End-to-end tests spawn tests/fake_openocd.py as a real subprocess speaking
the real Tcl RPC wire protocol over a real loopback socket (picked from the
ephemeral range so parallel test runs don't collide) — connect()/disconnect()
and every command are exercised against real process + socket machinery, not
mocks. Only the *responses* are canned, by fake_openocd.py's dispatch table.

Per wq#2630 (an acceptance probe must be able to fail): invalid-width,
invalid-reset-mode, and not-connected cases are probed explicitly so a
regression that silently accepts bad input fails loudly, not just the
happy path that was designed to pass.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openocd_client import OpenOCDClient, OpenOCDError, _parse_scan_chain

FAKE_OPENOCD = [sys.executable, str(Path(__file__).resolve().parent / "fake_openocd.py")]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def client():
    port = _free_port()
    c = OpenOCDClient(executable=sys.executable, tcl_port=port, startup_timeout=5.0)
    # inject the fake script as the first "config file" slot by overriding argv build:
    # OpenOCDClient always does [executable, -f, cfg, ...]; using the real python
    # interpreter as "executable" and the fake server script as the -f argument
    # would be wrong, so instead point executable straight at the script and pass
    # a dummy config (fake_openocd.py ignores -f values entirely).
    c._executable = str(Path(__file__).resolve().parent / "fake_openocd.py")
    c._tcl_port = port
    yield c
    if c.connected or c._proc is not None:
        c.disconnect()


def test_connect_disconnect_lifecycle(client):
    msg = client.connect(["dummy.cfg"])
    assert "connected" in msg
    assert client.connected
    out = client.disconnect()
    assert "disconnected" in out
    assert not client.connected


def test_connect_requires_config_files(client):
    with pytest.raises(ValueError):
        client.connect([])


def test_double_connect_rejected(client):
    client.connect(["dummy.cfg"])
    with pytest.raises(OpenOCDError):
        client.connect(["dummy.cfg"])


def test_commands_require_connection():
    c = OpenOCDClient()
    with pytest.raises(OpenOCDError):
        c.target_halt()


def test_target_halt_resume_reset(client):
    client.connect(["dummy.cfg"])
    assert "halted" in client.target_halt()
    assert client.target_resume() == ""
    assert "tap" in client.target_reset("init").lower()


def test_reset_rejects_invalid_mode(client):
    client.connect(["dummy.cfg"])
    with pytest.raises(ValueError):
        client.target_reset("bogus")


def test_read_memory_round_trip(client):
    client.connect(["dummy.cfg"])
    values = client.read_memory(0x20000000, width=32, count=3)
    assert values == [1, 2, 3]


def test_read_memory_rejects_invalid_width(client):
    client.connect(["dummy.cfg"])
    with pytest.raises(ValueError):
        client.read_memory(0x20000000, width=7)


def test_write_memory_rejects_empty_data(client):
    client.connect(["dummy.cfg"])
    with pytest.raises(ValueError):
        client.write_memory(0x20000000, [])


def test_write_memory_round_trip(client):
    client.connect(["dummy.cfg"])
    assert client.write_memory(0x20000000, [1, 2, 3]) == ""


def test_flash_write_missing_file_rejected(client):
    client.connect(["dummy.cfg"])
    with pytest.raises(FileNotFoundError):
        client.flash_write("/nonexistent/fw.bin", 0x08000000)


def test_flash_write_round_trip(client, tmp_path):
    client.connect(["dummy.cfg"])
    fw = tmp_path / "fw.bin"
    fw.write_bytes(b"\x00" * 16)
    out = client.flash_write(str(fw), 0x08000000)
    assert "wrote" in out and "verified" in out


def test_scan_chain_parses_fake_table(client):
    client.connect(["dummy.cfg"])
    taps = client.scan_chain()
    assert taps == [{
        "index": 0,
        "name": "stm32f4x.cpu",
        "enabled": True,
        "idcode": "0x4ba00477",
        "expected": "0x4ba00477",
        "ir_len": 4,
        "ir_cap": "0x01",
        "ir_mask": "0x0f",
    }]


def test_jtag_names(client):
    client.connect(["dummy.cfg"])
    assert client.jtag_names() == ["stm32f4x.cpu"]


def test_boundary_scan(client):
    client.connect(["dummy.cfg"])
    out = client.boundary_scan("stm32f4x.cpu", 0xFE, 8)
    assert out.split() == ["0", "1", "1", "0", "1", "0", "1", "0"]


def test_boundary_scan_rejects_nonpositive_length(client):
    client.connect(["dummy.cfg"])
    with pytest.raises(ValueError):
        client.boundary_scan("stm32f4x.cpu", 0xFE, 0)


def test_parse_scan_chain_ignores_header_and_separator():
    raw = (
        "   TapName             Enabled  IdCode     Expected   IrLen IrCap IrMask\n"
        "-- ------------------- -------- ---------- ---------- ----- ----- ------\n"
        " 0 foo.cpu               N      0x00000000 0x00000000    5  0x03  0x1f\n"
    )
    taps = _parse_scan_chain(raw)
    assert len(taps) == 1
    assert taps[0]["enabled"] is False
    assert taps[0]["ir_len"] == 5
