"""Unit tests for health.run_health_check with an injected runner (no real
subprocess). (wq#3810 lifecycle step 3.)"""
import subprocess

from health import run_health_check
from manifest import HealthCheck, PluginManifest


def mk(health=True):
    return PluginManifest(
        name="p", version="1.0", kind="tool",
        health=HealthCheck("probe", timeout_seconds=7) if health else None)


def test_no_health_check_is_healthy():
    res = run_health_check(mk(health=False), now=100.0)
    assert res.ok is True
    assert "no health check" in res.detail
    assert res.checked_at == 100.0


def test_exit_zero_is_healthy():
    res = run_health_check(mk(), now=1.0, runner=lambda cmd, t: (0, "all good"))
    assert res.ok is True
    assert res.detail == "healthy"


def test_nonzero_exit_is_unhealthy_with_tail():
    res = run_health_check(mk(), now=1.0, runner=lambda cmd, t: (3, "line1\nboom"))
    assert res.ok is False
    assert "exit 3" in res.detail and "boom" in res.detail


def test_timeout_is_unhealthy():
    def boom(cmd, t):
        raise subprocess.TimeoutExpired(cmd, t)
    res = run_health_check(mk(), now=1.0, runner=boom)
    assert res.ok is False
    assert "timed out after 7s" in res.detail


def test_runner_oserror_is_unhealthy():
    def boom(cmd, t):
        raise OSError("no such command")
    res = run_health_check(mk(), now=1.0, runner=boom)
    assert res.ok is False
    assert "failed to run" in res.detail
