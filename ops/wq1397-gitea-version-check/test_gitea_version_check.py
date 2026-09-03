#!/usr/bin/env python3
"""Offline tests for wq#1397 gitea_version_check.py. No real network calls:
fetch_version and ntfy-send are monkeypatched. Same self-contained
check()/_fails pattern as ops/wq1298-gitleaks-detect/test_gitleaks_detect.py.

Run: python3 test_gitea_version_check.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gitea_version_check as gvc

_fails = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        _fails.append(name)


def test_parse_version():
    check("plain semver", gvc.parse_version("1.27.3") == (1, 27, 3))
    check("two-part version", gvc.parse_version("1.27") == (1, 27, 0))
    check("build metadata stripped", gvc.parse_version("1.27.3+gitea-1.27.3") == (1, 27, 3))
    check("dev suffix stripped", gvc.parse_version("1.28.0-dev") == (1, 28, 0))


def test_version_comparison():
    check("patched version is safe", gvc.parse_version("1.27.3") >= gvc.parse_version("1.27.3"))
    check("newer version is safe", gvc.parse_version("1.28.0") >= gvc.parse_version("1.27.3"))
    check("older version is vulnerable", gvc.parse_version("1.27.2") < gvc.parse_version("1.27.3"))
    check("much older version is vulnerable", gvc.parse_version("1.20.0") < gvc.parse_version("1.27.3"))


def _run_with_mocks(monkeypatch_version, monkeypatch_error=None):
    ntfy_calls = []

    def fake_fetch_version(url, timeout=15):
        if monkeypatch_error:
            return None, monkeypatch_error
        return monkeypatch_version, None

    def fake_ntfy(title, message, priority="high", dry_run=False):
        ntfy_calls.append((title, message, priority))
        return True

    orig_fetch, orig_ntfy = gvc.fetch_version, gvc.ntfy
    gvc.fetch_version, gvc.ntfy = fake_fetch_version, fake_ntfy
    try:
        with tempfile.TemporaryDirectory() as d:
            state_path = os.path.join(d, "state.json")
            rc1 = gvc.run(state_path=state_path)
            rc2 = gvc.run(state_path=state_path)
    finally:
        gvc.fetch_version, gvc.ntfy = orig_fetch, orig_ntfy
    return rc1, rc2, ntfy_calls


def test_safe_version_no_alert_and_exit_zero():
    rc1, rc2, calls = _run_with_mocks("1.27.3")
    check("exit 0 on safe version", rc1 == 0 and rc2 == 0)
    check("no alert on repeated safe status", len(calls) == 0)


def test_vulnerable_version_alerts_once():
    rc1, rc2, calls = _run_with_mocks("1.27.2")
    check("exit 1 on vulnerable version", rc1 == 1 and rc2 == 1)
    check("alerts exactly once (transition-based)", len(calls) == 1)
    check("alert mentions the CVE", "CVE-2026-66877" in calls[0][1])
    check("alert is high priority", calls[0][2] == "high")


def test_unreachable_alerts_once():
    rc1, rc2, calls = _run_with_mocks(None, monkeypatch_error="Connection refused")
    check("exit 1 when unreachable", rc1 == 1 and rc2 == 1)
    check("alerts exactly once for unreachable", len(calls) == 1)


def test_recovery_alert_on_transition_back_to_safe():
    ntfy_calls = []

    def fake_ntfy(title, message, priority="high", dry_run=False):
        ntfy_calls.append((title, message, priority))
        return True

    versions = iter(["1.27.2", "1.27.3"])

    def fake_fetch_version(url, timeout=15):
        return next(versions), None

    orig_fetch, orig_ntfy = gvc.fetch_version, gvc.ntfy
    gvc.fetch_version, gvc.ntfy = fake_fetch_version, fake_ntfy
    try:
        with tempfile.TemporaryDirectory() as d:
            state_path = os.path.join(d, "state.json")
            gvc.run(state_path=state_path)
            gvc.run(state_path=state_path)
    finally:
        gvc.fetch_version, gvc.ntfy = orig_fetch, orig_ntfy

    check("two alerts: regression then recovery", len(ntfy_calls) == 2)
    check("second alert is the recovery notice", "recovered" in ntfy_calls[1][0])


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"{name}:")
            fn()
    if _fails:
        print(f"\n{len(_fails)} failure(s): {_fails}")
        return 1
    print("\nall tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
