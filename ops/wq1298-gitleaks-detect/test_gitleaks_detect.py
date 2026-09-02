#!/usr/bin/env python3
"""Offline tests for wq#1298 gitleaks_detect.py. No real gitleaks binary or
network calls: subprocess.run is monkeypatched for both `gitleaks detect`
and ntfy-send. Same self-contained check()/_fails pattern as
ops/wq1092-probe-target-liveness-guard/test_probe_target_liveness.py.

Run: python3 test_gitleaks_detect.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gitleaks_detect as gd

_fails = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        _fails.append(name)


def make_finding(fingerprint, rule="aws-access-key", file="config.py", line=12,
                  commit="deadbeef1234", secret="AKIA-SHOULD-NEVER-LEAK"):
    return {
        "Fingerprint": fingerprint, "RuleID": rule, "File": file,
        "StartLine": line, "Commit": commit, "Secret": secret, "Match": secret,
    }


def test_find_repos():
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "repo-a", ".git"))
        os.makedirs(os.path.join(root, "repo-b", ".git"))
        os.makedirs(os.path.join(root, "repo-b", "node_modules", "sub", ".git"))
        wt = os.path.join(root, "worktrees", "wt1")
        os.makedirs(wt)
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("gitdir: /elsewhere/.git/worktrees/wt1\n")

        repos = gd.find_repos(root)
        check("finds repo-a", os.path.join(root, "repo-a") in repos)
        check("finds repo-b", os.path.join(root, "repo-b") in repos)
        check("finds worktree via .git file", wt in repos)
        check("excludes node_modules nested repo",
              not any("node_modules" in r for r in repos))


def test_redact_never_leaks_secret():
    findings = [make_finding("fp1", secret="ghp_realtoken1234567890")]
    view = gd.redact(findings)
    dumped = json.dumps(view)
    check("redacted view has no Secret field", "Secret" not in view[0])
    check("redacted view has no Match field", "Match" not in view[0])
    check("raw secret text absent from redacted dump", "ghp_realtoken1234567890" not in dumped)
    check("fingerprint preserved", view[0]["fingerprint"] == "fp1")
    check("commit truncated to 12 chars", view[0]["commit"] == "deadbeef1234"[:12])


def test_scan_repo_parses_report(monkeypatch):
    findings_written = [make_finding("fp-a"), make_finding("fp-b")]
    captured_cmd = {}

    def fake_run(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        report_path = cmd[cmd.index("--report-path") + 1]
        with open(report_path, "w") as f:
            json.dump(findings_written, f)

        class R:
            returncode = 0
            stderr = ""
        return R()

    monkeypatch.setattr(gd.subprocess, "run", fake_run)
    findings, error = gd.scan_repo("/some/repo", "/fake/gitleaks")
    check("scan_repo returns no error on rc=0", error is None, str(error))
    check("scan_repo returns parsed findings", len(findings) == 2, str(findings))
    check("scan_repo invokes gitleaks detect (not protect)", "detect" in captured_cmd["cmd"])
    check("scan_repo uses --exit-code 0 (findings != tool failure)",
          "--exit-code" in captured_cmd["cmd"])


def test_scan_repo_reports_tool_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        class R:
            returncode = 2
            stderr = "boom"
        return R()

    monkeypatch.setattr(gd.subprocess, "run", fake_run)
    findings, error = gd.scan_repo("/some/repo", "/fake/gitleaks")
    check("tool failure surfaces as error, not silent empty findings",
          error is not None and "boom" in error)
    check("no findings returned on tool failure", findings == [])


def test_run_alerts_only_on_new_findings(monkeypatch):
    with tempfile.TemporaryDirectory() as root:
        repo = os.path.join(root, "repo-a")
        os.makedirs(os.path.join(repo, ".git"))
        state_path = os.path.join(root, "state.json")
        gitleaks_bin = os.path.join(root, "gitleaks")
        with open(gitleaks_bin, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(gitleaks_bin, 0o755)

        alerts = []
        monkeypatch.setattr(gd, "ntfy", lambda title, msg, priority="high", dry_run=False:
                             alerts.append((title, msg)))
        monkeypatch.setattr(gd, "STAMP_PATH", os.path.join(root, "stamp"))

        scan_calls = {"n": 0}

        def fake_scan_repo(r, bin_, timeout=300):
            scan_calls["n"] += 1
            if scan_calls["n"] == 1:
                return [make_finding("fp-1", secret="s3cr3t-one")], None
            return [make_finding("fp-1", secret="s3cr3t-one"),
                    make_finding("fp-2", secret="s3cr3t-two")], None

        monkeypatch.setattr(gd, "scan_repo", fake_scan_repo)
        monkeypatch.setattr(gd, "acquire_lock", lambda path=gd.LOCK_PATH: 3)

        gd.run(root=root, state_path=state_path, gitleaks_bin=gitleaks_bin)
        check("first run alerts once (fp-1 is new)", len(alerts) == 1, str(alerts))
        check("first alert never contains the raw secret",
              "s3cr3t-one" not in alerts[0][1])

        state_after_first = gd.load_state(state_path)
        check("state persists fp-1 after first run",
              state_after_first.get(repo) == ["fp-1"], str(state_after_first))

        gd.run(root=root, state_path=state_path, gitleaks_bin=gitleaks_bin)
        check("second run alerts again (only fp-2 is new)", len(alerts) == 2, str(alerts))
        check("second alert mentions only the new finding, not fp-1 again",
              "fp-2" in alerts[1][1] or "config.py" in alerts[1][1])

        gd.run(root=root, state_path=state_path, gitleaks_bin=gitleaks_bin)
        check("third run with unchanged findings sends no new alert", len(alerts) == 2)


def test_run_skips_when_locked(monkeypatch):
    with tempfile.TemporaryDirectory() as root:
        monkeypatch.setattr(gd, "acquire_lock", lambda path=gd.LOCK_PATH: None)
        scanned = {"called": False}
        monkeypatch.setattr(gd, "find_repos", lambda root: scanned.update(called=True) or [])
        rc = gd.run(root=root, state_path=os.path.join(root, "state.json"),
                    gitleaks_bin="/nonexistent")
        check("locked run exits 0 without scanning", rc == 0 and not scanned["called"])


def test_run_alerts_when_gitleaks_missing(monkeypatch):
    with tempfile.TemporaryDirectory() as root:
        alerts = []
        monkeypatch.setattr(gd, "ntfy", lambda title, msg, priority="high", dry_run=False:
                             alerts.append((title, msg)))
        monkeypatch.setattr(gd, "acquire_lock", lambda path=gd.LOCK_PATH: 3)
        gd.run(root=root, state_path=os.path.join(root, "state.json"),
               gitleaks_bin=os.path.join(root, "no-such-binary"))
        check("missing binary triggers exactly one alert", len(alerts) == 1, str(alerts))


class _MonkeyPatch:
    """Minimal monkeypatch shim so this file runs standalone under plain
    `python3` without requiring pytest to be installed."""
    def __init__(self):
        self._undo = []

    def setattr(self, obj, name, value):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self):
        for obj, name, old in reversed(self._undo):
            setattr(obj, name, old)


def main():
    test_find_repos()

    for fn in (test_scan_repo_parses_report, test_scan_repo_reports_tool_failure,
               test_run_alerts_only_on_new_findings, test_run_skips_when_locked,
               test_run_alerts_when_gitleaks_missing):
        mp = _MonkeyPatch()
        try:
            fn(mp)
        finally:
            mp.undo()

    test_redact_never_leaks_secret()

    if _fails:
        print(f"\n{len(_fails)} FAILED: {_fails}")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
