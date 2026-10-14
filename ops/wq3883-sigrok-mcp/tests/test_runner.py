"""Unit tests for the runner.py subprocess boundary (wq#3883)."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner import SigrokRunError, run  # noqa: E402


def test_run_missing_binary_raises_without_subprocess(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(1))
    with pytest.raises(SigrokRunError):
        run(["sigrok-cli", "--scan"])
    assert called == []  # never reached subprocess.run


def test_run_passes_argv_without_shell(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sigrok-cli")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    proc = run(["sigrok-cli", "--scan"], timeout=5)
    assert proc.stdout == "ok"
    assert seen["argv"] == ["sigrok-cli", "--scan"]
    assert seen["kwargs"]["shell"] is False
    assert seen["kwargs"]["timeout"] == 5


def test_run_nonzero_exit_raises_with_stderr(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sigrok-cli")

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no such device")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(SigrokRunError) as excinfo:
        run(["sigrok-cli", "--scan"])
    assert excinfo.value.returncode == 1
    assert "no such device" in excinfo.value.stderr
