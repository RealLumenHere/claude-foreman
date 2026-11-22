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

_ENERGIZE_CONFIRM_PATH = Path(__file__).resolve().parents[3] / "harness" / "energize_confirm.py"
_ec_spec = importlib.util.spec_from_file_location("energize_confirm_integration", _ENERGIZE_CONFIRM_PATH)
energize_confirm = importlib.util.module_from_spec(_ec_spec)
sys.modules["energize_confirm_integration"] = energize_confirm
_ec_spec.loader.exec_module(energize_confirm)
sys.path.insert(0, str(_ENERGIZE_CONFIRM_PATH.parent / "tests"))
import ec_testkit as ec_kit  # noqa: E402

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


def test_read_write_verify_round_trip_real_binary(tmp_path, monkeypatch):
    dump = tmp_path / "full.bin"
    read_result = ops.read_flash(PROGRAMMER, str(dump))
    assert read_result["ok"] is True
    assert dump.stat().st_size == 16384 * 1024

    # wq#4747: write_flash now refuses without a real operator-issued confirm
    # token. Mint one end-to-end (no mocking) against this worktree's own
    # energize_confirm.py, with a scratch verifier/token dir so this test never
    # touches the live store. wq#5216: the module reads no env var, so the
    # verify subprocess runs through ec_testkit's patching wrapper.
    token_dir, verifier = ec_kit.install(energize_confirm, tmp_path, monkeypatch)
    monkeypatch.setattr(ops, "ENERGIZE_CONFIRM_SCRIPT",
                        Path(ec_kit.wrapper(tmp_path, token_dir, verifier, "ec")))
    token = ec_kit.mint(energize_confirm, "flashrom.write_flash")

    write_result = ops.write_flash(PROGRAMMER, str(dump), confirm_token=token)
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
