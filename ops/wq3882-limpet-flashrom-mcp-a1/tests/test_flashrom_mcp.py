"""wq#3882 — tests for ops/wq3882-limpet-flashrom-mcp-a1/flashrom_ops.py.

flashrom_ops.py (not server.py) holds the testable logic, since the `mcp` package isn't
pip-installed in this build lane (same split ops/wq1151-lumen-kb uses for the same
reason). No real flashrom binary or hardware is exercised: subprocess.run is
monkeypatched to a fake that records the argv it was given and returns a canned
flashrom-shaped CompletedProcess, so these assert on what gets executed (and that
destructive ops refuse without confirm=True), not on live hardware behaviour this
sandbox cannot provide.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_OPS_PATH = Path(__file__).resolve().parent.parent / "flashrom_ops.py"
_spec = importlib.util.spec_from_file_location("flashrom_ops", _OPS_PATH)
ops = importlib.util.module_from_spec(_spec)
sys.modules["flashrom_ops"] = ops
_spec.loader.exec_module(ops)


class FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def fake_run(monkeypatch):
    calls = []

    def _fake(command, **kwargs):
        calls.append(command)
        return FakeCompletedProcess(
            returncode=0,
            stdout='Found Winbond flash chip "W25Q128.V" (16384 kB, SPI).\nNo operations were specified.\n',
        )

    monkeypatch.setattr(subprocess, "run", _fake)
    return calls


def test_detect_chip_builds_argv_and_parses_chip(fake_run):
    result = ops.detect_chip("ch341a_spi")
    assert fake_run[0] == [ops.FLASHROM_BIN, "-p", "ch341a_spi"]
    assert result["ok"] is True
    assert result["chips"] == [
        {"vendor": "Winbond", "chip": "W25Q128.V", "size_kb": 16384, "bus": "SPI"}
    ]


def test_detect_chip_passes_explicit_chip_override(fake_run):
    ops.detect_chip("ch341a_spi", chip="W25Q128.V")
    assert fake_run[0] == [ops.FLASHROM_BIN, "-p", "ch341a_spi", "-c", "W25Q128.V"]


def test_read_flash_creates_parent_dir_and_passes_path(fake_run, tmp_path):
    out = tmp_path / "dumps" / "backup.bin"
    result = ops.read_flash("ch341a_spi", str(out))
    assert out.parent.is_dir()
    assert fake_run[0] == [ops.FLASHROM_BIN, "-p", "ch341a_spi", "-r", str(out)]
    assert result["output_path"] == str(out)


def test_read_flash_with_region(fake_run, tmp_path):
    out = tmp_path / "bios_region.bin"
    ops.read_flash("ch341a_spi", str(out), region="bios")
    assert fake_run[0] == [ops.FLASHROM_BIN, "-p", "ch341a_spi", "-r", str(out), "-i", "bios"]


def test_write_flash_requires_existing_file(fake_run, tmp_path):
    missing = tmp_path / "nope.bin"
    with pytest.raises(FileNotFoundError):
        ops.write_flash("ch341a_spi", str(missing))
    assert fake_run == []  # never shelled out for a file that doesn't exist


def test_write_flash_default_verifies(fake_run, tmp_path):
    src = tmp_path / "image.bin"
    src.write_bytes(b"\xff" * 16)
    ops.write_flash("ch341a_spi", str(src))
    assert fake_run[0] == [ops.FLASHROM_BIN, "-p", "ch341a_spi", "-w", str(src)]


def test_write_flash_noverify_appends_flag(fake_run, tmp_path):
    src = tmp_path / "image.bin"
    src.write_bytes(b"\xff" * 16)
    ops.write_flash("ch341a_spi", str(src), verify=False)
    assert fake_run[0] == [ops.FLASHROM_BIN, "-p", "ch341a_spi", "-w", str(src), "-n"]


def test_verify_flash_requires_existing_file(fake_run, tmp_path):
    missing = tmp_path / "nope.bin"
    with pytest.raises(FileNotFoundError):
        ops.verify_flash("ch341a_spi", str(missing))
    assert fake_run == []


def test_verify_flash_argv(fake_run, tmp_path):
    src = tmp_path / "image.bin"
    src.write_bytes(b"\xff" * 16)
    ops.verify_flash("ch341a_spi", str(src))
    assert fake_run[0] == [ops.FLASHROM_BIN, "-p", "ch341a_spi", "-v", str(src)]


def test_erase_chip_refuses_without_confirm(fake_run):
    with pytest.raises(ops.FlashromError, match="confirm=True"):
        ops.erase_chip("ch341a_spi")
    assert fake_run == []  # the refusal must happen before any subprocess call


def test_erase_chip_runs_with_confirm(fake_run):
    result = ops.erase_chip("ch341a_spi", confirm=True)
    assert fake_run[0] == [ops.FLASHROM_BIN, "-p", "ch341a_spi", "-E"]
    assert result["ok"] is True


def test_identify_programmer_filters_to_flash_programmer(monkeypatch, tmp_path):
    hal = tmp_path / "hal.py"
    hal.write_text("#!/usr/bin/env python3\n")  # never executed; subprocess.run is faked
    monkeypatch.setattr(ops, "HAL_SCRIPT", hal)

    devices = [
        {"hw_type": "sdr_receiver", "name": "RTL-SDR"},
        {"hw_type": "flash_programmer", "name": "CH341A", "device_node": "/dev/bus/usb/001/004"},
    ]

    def _fake(command, **kwargs):
        assert command[-1] == "detect"
        return FakeCompletedProcess(returncode=0, stdout=json.dumps(devices))

    monkeypatch.setattr(subprocess, "run", _fake)
    result = ops.identify_programmer()
    assert result == [devices[1]]


def test_identify_programmer_missing_hal_script_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(ops, "HAL_SCRIPT", tmp_path / "does-not-exist.py")
    with pytest.raises(ops.FlashromError, match="HAL script not found"):
        ops.identify_programmer()


def test_timeout_raises_flashrom_error(monkeypatch):
    def _fake(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", _fake)
    with pytest.raises(ops.FlashromError, match="timed out"):
        ops.detect_chip("ch341a_spi")


def test_missing_binary_raises_flashrom_error(monkeypatch):
    def _fake(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(subprocess, "run", _fake)
    with pytest.raises(ops.FlashromError, match="not found"):
        ops.detect_chip("ch341a_spi")


def test_nonzero_exit_does_not_raise_but_reports_failure(monkeypatch):
    def _fake(command, **kwargs):
        return FakeCompletedProcess(returncode=1, stdout="", stderr="Programmer initialization failed.\n")

    monkeypatch.setattr(subprocess, "run", _fake)
    result = ops.detect_chip("ch341a_spi")
    assert result["ok"] is False
    assert result["returncode"] == 1
    assert "Programmer initialization failed" in result["stderr"]
