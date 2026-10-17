#!/usr/bin/env python3
"""Tests for classify_access.py (wq#4143).

Pure-logic tests exercise the ported classify_dp_access()/decode_idcode()/
looks_like_vector_table() against synthetic inputs covering all three
verdicts. A socket-based integration test runs classify_access() against an
in-process fake OpenOCD Tcl RPC server (same wire format as
ops/wq3881-openocd-mcp/tests/fake_openocd.py) to exercise OpenOCDProbe's
parsing without needing real hardware or OpenOCD installed.
"""
from __future__ import annotations

import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp"))

import classify_access as ca  # noqa: E402

TERM = b"\x1a"


def test_classify_dead_when_no_dp_response():
    classification, reason = ca.classify_dp_access(
        dp_responds=False, halted=False, flash_reads=[], vector_table_plausible=False
    )
    assert classification == "DEAD"
    assert "No DP/TAP response" in reason


def test_classify_open_when_vector_table_plausible_and_halted():
    classification, _ = ca.classify_dp_access(
        dp_responds=True, halted=True, flash_reads=[], vector_table_plausible=True
    )
    assert classification == "OPEN"


def test_classify_locked_when_halted_reads_are_sentinel():
    flash_reads = [{"all_ones": True, "all_zero": False}]
    classification, reason = ca.classify_dp_access(
        dp_responds=True, halted=True, flash_reads=flash_reads, vector_table_plausible=False
    )
    assert classification == "LOCKED"
    assert "readout protection" in reason


def test_classify_open_when_halted_reads_are_not_sentinel():
    flash_reads = [{"all_ones": False, "all_zero": False}]
    classification, _ = ca.classify_dp_access(
        dp_responds=True, halted=True, flash_reads=flash_reads, vector_table_plausible=False
    )
    assert classification == "OPEN"


def test_classify_locked_when_dp_responds_but_halt_failed():
    classification, reason = ca.classify_dp_access(
        dp_responds=True, halted=False, flash_reads=[], vector_table_plausible=False
    )
    assert classification == "LOCKED"
    assert "RDP / CRP / APPROTECT" in reason


def test_decode_idcode_arm_coresight_jtag_dp():
    # 0x4ba00477 is the generic ARM CoreSight JTAG-DP IDCODE shared by many
    # Cortex-M chips' cpu TAP (incl. STM32F4) -- the cpu TAP's designer field
    # identifies ARM (who designed the JTAG-DP), not the silicon vendor.
    decoded = ca.decode_idcode(0x4BA00477)
    assert decoded["vendor"] == "ARM"
    assert decoded["raw"] == "0x4BA00477"


def test_decode_idcode_unknown_vendor():
    decoded = ca.decode_idcode(0x00000001)
    assert decoded["vendor"] == "Unknown"


def test_looks_like_vector_table_true_for_plausible_sram_and_thumb_reset():
    assert ca.looks_like_vector_table([0x20001000, 0x08000201]) is True


def test_looks_like_vector_table_false_for_sentinel_words():
    assert ca.looks_like_vector_table([0xFFFFFFFF, 0xFFFFFFFF]) is False


def test_looks_like_vector_table_false_for_short_input():
    assert ca.looks_like_vector_table([0x20001000]) is False


def _dispatch(cmd: str) -> str:
    if cmd == "scan_chain":
        return (
            "   TapName             Enabled  IdCode     Expected   IrLen IrCap IrMask\n"
            "-- ------------------- -------- ---------- ---------- ----- ----- ------\n"
            " 0 stm32f4x.cpu          Y      0x4ba00477 0x4ba00477    4  0x01  0x0f\n"
        )
    if cmd == "halt":
        return "target halted due to debug-request, current mode: Thread"
    if cmd.startswith("read_memory 0x08000000"):
        return "0x20001000 0x08000201 0x12345678 0x9abcdef0"
    if cmd.startswith("read_memory"):
        return "0xFFFFFFFF 0xFFFFFFFF 0xFFFFFFFF 0xFFFFFFFF"
    return ""


def _serve_one(srv: socket.socket) -> None:
    conn, _ = srv.accept()
    buf = b""
    with conn:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                return
            buf += chunk
            if not buf.endswith(TERM):
                continue
            cmd = buf[:-1].decode()
            buf = b""
            conn.sendall(_dispatch(cmd).encode() + TERM)


def test_classify_access_end_to_end_open_via_fake_tcl_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)
    t = threading.Thread(target=_serve_one, args=(srv,), daemon=True)
    t.start()

    try:
        result = ca.classify_access(host="127.0.0.1", port=port, flash_bases=[0x08000000])
    finally:
        srv.close()

    assert result["classification"] == "OPEN"
    assert result["vendor"] == "ARM"
    assert result["evidence"]["halted"] is True


def test_classify_access_dead_when_unreachable():
    result = ca.classify_access(host="127.0.0.1", port=1)  # port 1 refuses connections
    assert result["classification"] == "DEAD"
