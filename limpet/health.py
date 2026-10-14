"""LIMPET health checks (lifecycle step 3). Each plugin ships its own health
command in its manifest's [health] table; the registry runs it on the
plugin's cadence and records healthy/unhealthy. A plugin with no health check
is always healthy -- there is nothing to probe.

Isolated here so registry_core.py stays pure: this is the only module that
runs a subprocess. Ref: design/wq3810_limpet_plugin_system.md, manifest.py.
"""
from __future__ import annotations

import dataclasses
import subprocess
import time

from manifest import PluginManifest


@dataclasses.dataclass(frozen=True)
class HealthResult:
    name: str
    ok: bool
    detail: str
    checked_at: float


def run_health_check(manifest: PluginManifest, now=None, runner=None) -> HealthResult:
    """Run one plugin's health command and classify the outcome.

    now:    injectable clock (seconds), for deterministic tests.
    runner: injectable subprocess callable(cmd, timeout) -> (exit_code, output);
            defaults to a real shell invocation. Tests pass a fake to avoid I/O.
    """
    now = time.time() if now is None else now
    hc = manifest.health
    if hc is None:
        return HealthResult(manifest.name, True, "no health check declared", now)

    runner = _shell_runner if runner is None else runner
    try:
        code, output = runner(hc.command, hc.timeout_seconds)
    except subprocess.TimeoutExpired:
        return HealthResult(
            manifest.name, False,
            f"health check timed out after {hc.timeout_seconds}s", now)
    except OSError as exc:
        return HealthResult(manifest.name, False, f"health check failed to run: {exc}", now)

    ok = code == 0
    tail = (output or "").strip().splitlines()[-1:] or [""]
    detail = "healthy" if ok else f"exit {code}: {tail[0]}"
    return HealthResult(manifest.name, ok, detail, now)


def _shell_runner(command: str, timeout: int):
    """Default runner: run the health command through the shell, capture a
    small amount of output for the detail line. Never raises on non-zero
    exit -- a failing check is data, not an error."""
    proc = subprocess.run(
        command, shell=True, timeout=timeout,
        capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
