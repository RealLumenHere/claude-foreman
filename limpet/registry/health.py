"""wq#3807 — run each tool's health_check command and report pass/fail.

Lets the agent (or an operator) confirm the manifest is not lying: a tool that
registered a capability but no longer runs shows up here as a failed probe
rather than as a silent dead end mid-task.
"""

from __future__ import annotations

import subprocess


def run_health_check(command: str, *, timeout: float = 20.0) -> dict:
    """Run one health_check command. Returns a result dict, never raises."""
    try:
        proc = subprocess.run(command, shell=True, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit_code": None, "reason": f"timed out after {timeout}s"}
    except OSError as exc:
        return {"ok": False, "exit_code": None, "reason": str(exc)}
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "reason": "" if proc.returncode == 0 else (proc.stderr.strip()[:200] or "nonzero exit"),
    }


def check_tools(tools: list[dict], *, timeout: float = 20.0) -> list[dict]:
    """Run the health_check for each tool. Returns one result row per tool."""
    results = []
    for tool in tools:
        result = run_health_check(tool["health_check"], timeout=timeout)
        results.append({"name": tool["name"], **result,
                        "command": tool["health_check"]})
    return results
