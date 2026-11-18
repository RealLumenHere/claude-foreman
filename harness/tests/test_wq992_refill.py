"""wq#992-B — trigger_refill() kicks a dispatch pass on slot-freeing exit, guarded.

Pure unit tests: subprocess and os.path.exists are monkeypatched so nothing real is
spawned. Covers the two kill switches, the no-refill escape hatch, the command shape,
and the fail-open contract (a broken kick never raises into the worker's exit path).
"""
import os
import sys

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS)
import fworker  # noqa: E402


class _Done:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


def _no_files(monkeypatch):
    monkeypatch.setattr(fworker.os.path, "exists", lambda p: False)


def test_no_refill_env_suppresses(monkeypatch):
    calls = []
    monkeypatch.setenv("FOREMAN_NO_REFILL", "1")
    monkeypatch.setattr(fworker.subprocess, "run",
                        lambda *a, **k: calls.append(a) or _Done())
    fworker.trigger_refill(123, "done")
    assert calls == []


def test_disarm_file_suppresses(monkeypatch):
    calls = []
    monkeypatch.delenv("FOREMAN_NO_REFILL", raising=False)
    monkeypatch.setattr(fworker.os.path, "exists",
                        lambda p: p == fworker.DISPATCH_DISARM_PATH)
    monkeypatch.setattr(fworker.subprocess, "run",
                        lambda *a, **k: calls.append(a) or _Done())
    fworker.trigger_refill(123, "done")
    assert calls == []


def test_stop_file_suppresses(monkeypatch):
    calls = []
    monkeypatch.delenv("FOREMAN_NO_REFILL", raising=False)
    monkeypatch.setattr(fworker.os.path, "exists",
                        lambda p: p == fworker.STOP_FILE)
    monkeypatch.setattr(fworker.subprocess, "run",
                        lambda *a, **k: calls.append(a) or _Done())
    fworker.trigger_refill(123, "done")
    assert calls == []


def test_kicks_dispatch_service_detached(monkeypatch):
    captured = {}
    monkeypatch.delenv("FOREMAN_NO_REFILL", raising=False)
    _no_files(monkeypatch)

    def fake_run(cmd, **k):
        captured["cmd"] = cmd
        return _Done(0)

    monkeypatch.setattr(fworker.subprocess, "run", fake_run)
    fworker.trigger_refill(992, "done")
    cmd = captured["cmd"]
    # detached, privileged, garbage-collected transient unit
    assert cmd[:3] == ["sudo", "-n", "systemd-run"]
    assert "--collect" in cmd
    # settle sleep + start the dispatch service, no-block so the kick returns at once
    joined = " ".join(cmd)
    assert "sleep" in joined
    assert f"systemctl start --no-block {fworker.DISPATCH_SERVICE}" in joined
    # unit name is ticket-scoped so concurrent completions don't collide
    assert any("fdisp-kick-wq992" in c for c in cmd)


def test_fail_open_on_launch_error(monkeypatch):
    monkeypatch.delenv("FOREMAN_NO_REFILL", raising=False)
    _no_files(monkeypatch)

    def boom(*a, **k):
        raise OSError("systemd-run missing")

    monkeypatch.setattr(fworker.subprocess, "run", boom)
    # contract: a failed kick is logged and swallowed, never raised into the exit path
    fworker.trigger_refill(992, "done")


def test_nonzero_kick_is_swallowed(monkeypatch):
    monkeypatch.delenv("FOREMAN_NO_REFILL", raising=False)
    _no_files(monkeypatch)
    monkeypatch.setattr(fworker.subprocess, "run",
                        lambda *a, **k: _Done(1, stderr="denied"))
    fworker.trigger_refill(992, "done")


def test_superseded_broker_kicks_auto_dispatch(monkeypatch):
    """wq#5084: with BROKER_SUPERSEDED present the kick starts lumen-auto-dispatch.service,
    never the retired foreman broker (2026-11-18: refill kicks ran it over every lane)."""
    captured = {}
    monkeypatch.delenv("FOREMAN_NO_REFILL", raising=False)
    monkeypatch.setattr(fworker.os.path, "exists",
                        lambda p: p == fworker.BROKER_SUPERSEDED_PATH)
    monkeypatch.setattr(fworker.subprocess, "run",
                        lambda cmd, **k: captured.setdefault("cmd", cmd) and _Done(0))
    fworker.trigger_refill(5084, "done")
    joined = " ".join(captured["cmd"])
    assert f"systemctl start --no-block {fworker.AUTO_DISPATCH_SERVICE}" in joined
    assert fworker.DISPATCH_SERVICE not in joined


def test_superseded_still_honors_disarm(monkeypatch):
    calls = []
    monkeypatch.delenv("FOREMAN_NO_REFILL", raising=False)
    monkeypatch.setattr(fworker.os.path, "exists",
                        lambda p: p in (fworker.BROKER_SUPERSEDED_PATH, fworker.DISPATCH_DISARM_PATH))
    monkeypatch.setattr(fworker.subprocess, "run",
                        lambda *a, **k: calls.append(a) or _Done())
    fworker.trigger_refill(5084, "done")
    assert calls == []
