"""Unit tests for oom_forensics.py (wq#648/S3: journal-based OOM corroboration).
Best-effort by design — a bad/missing journalctl must never raise, only degrade
the evidence returned. classifier.py's exit-137 verdict never depends on this.
"""
import subprocess
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import oom_forensics as of  # noqa: E402


def test_missing_journalctl_binary_is_best_effort(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("no such file")
    monkeypatch.setattr(subprocess, "run", boom)
    result = of.oom_journal_evidence(time.time())
    assert result == {"persistent": False, "matched": False, "lines": []}


def test_journalctl_timeout_is_best_effort(monkeypatch):
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="journalctl", timeout=5)
    monkeypatch.setattr(subprocess, "run", timeout)
    result = of.oom_journal_evidence(time.time())
    assert result["matched"] is False
    assert result["lines"] == []


def test_matches_oom_patterns_and_caps_line_count(monkeypatch):
    lines = [f"Out of memory: Killed process {1000 + i} (claude)" for i in range(30)]
    lines.append("unrelated kernel line")

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["journalctl", "--header"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="File path: /var/log/journal/x/system.journal\n")
        return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(lines))
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = of.oom_journal_evidence(time.time())
    assert result["persistent"] is True
    assert result["matched"] is True
    assert len(result["lines"]) == 20  # capped


def test_pid_filter_narrows_to_matching_lines(monkeypatch):
    lines = [
        "Out of memory: Killed process 4242 (claude)",
        "Out of memory: Killed process 9999 (something-else)",
    ]

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["journalctl", "--header"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="")
        return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(lines))
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = of.oom_journal_evidence(time.time(), pid=4242)
    assert result["matched"] is True
    assert len(result["lines"]) == 1
    assert "4242" in result["lines"][0]


def test_no_match_returns_empty_but_not_an_error(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="totally unrelated log line\n")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = of.oom_journal_evidence(time.time())
    assert result["matched"] is False
    assert result["lines"] == []
