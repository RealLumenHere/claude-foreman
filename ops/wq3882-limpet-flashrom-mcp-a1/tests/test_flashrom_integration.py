"""wq#3894 — integration tests against the real `flashrom` binary.

Unlike test_flashrom_mcp.py (subprocess.run monkeypatched), these run flashrom's
`dummy` programmer (emulate=W25Q128FV), which behaves like a real SPI chip without
hardware. This is what wq#3890's judgment report used to prove read_flash's region=
was unconditionally broken (reports/3890_judgment_wq3882_flashrom_region_flag.md) —
a mock can't catch a bug in what flashrom itself accepts. Skipped when flashrom isn't
installed.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

_OPS_PATH = Path(__file__).resolve().parent.parent / "flashrom_ops.py"
_spec = importlib.util.spec_from_file_location("flashrom_ops_integration", _OPS_PATH)
ops = importlib.util.module_from_spec(_spec)
sys.modules["flashrom_ops_integration"] = ops
_spec.loader.exec_module(ops)

pytestmark = pytest.mark.skipif(
    shutil.which("flashrom") is None, reason="flashrom binary not installed"
)

PROGRAMMER = "dummy:emulate=W25Q128FV"


def test_detect_chip_real_binary():
    result = ops.detect_chip(PROGRAMMER)
    assert result["ok"] is True
    assert result["chips"] == [
        {"vendor": "Winbond", "chip": "W25Q128.V", "size_kb": 16384, "bus": "SPI"}
    ]


def test_read_write_verify_round_trip_real_binary(tmp_path):
    dump = tmp_path / "full.bin"
    read_result = ops.read_flash(PROGRAMMER, str(dump))
    assert read_result["ok"] is True
    assert dump.stat().st_size == 16384 * 1024

    write_result = ops.write_flash(PROGRAMMER, str(dump))
    assert write_result["ok"] is True

    verify_result = ops.verify_flash(PROGRAMMER, str(dump))
    assert verify_result["ok"] is True


def test_read_flash_region_without_layout_fails_loudly_real_binary(tmp_path):
    out = tmp_path / "region_no_layout.bin"
    with pytest.raises(ops.FlashromError, match="layout_file"):
        ops.read_flash(PROGRAMMER, str(out), region="bios")
    assert not out.exists()


def test_read_flash_region_with_layout_succeeds_real_binary(tmp_path):
    layout = tmp_path / "board.layout"
    layout.write_text("0x000000:0xffffff bios\n")
    out = tmp_path / "region.bin"

    result = ops.read_flash(PROGRAMMER, str(out), region="bios", layout_file=str(layout))

    assert result["ok"] is True, result
    assert out.stat().st_size == 16384 * 1024
