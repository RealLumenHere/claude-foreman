#!/usr/bin/env python3
"""Tests for the agent kill switch (wq#4929).

Runs entirely in temp dirs — no real fworker, no real wq, no real token-engine.
Tests the orchestration logic, marker protocol, idempotency, and unkill.
"""

import json
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agent_kill import KillSwitch, unkill, is_killed, _kill_marker, _find_worktrees


FIXTURE_TICKET = 9999


@pytest.fixture
def env(tmp_path, monkeypatch):
    marker = _kill_marker(FIXTURE_TICKET)
    if os.path.exists(marker):
        os.unlink(marker)

    runs = tmp_path / "_runs" / f"wq{FIXTURE_TICKET}"
    runs.mkdir(parents=True)
    (runs / "attempt-1.json").write_text('{"status": "KILLED"}')

    events = tmp_path / "events"
    events.mkdir()
    (events / f"fworker-wq{FIXTURE_TICKET}.jsonl").write_text('{"event":"test"}\n')

    incidents = tmp_path / "incidents"
    incidents.mkdir()

    worktrees = tmp_path / "worktrees"
    wt = worktrees / f"wq{FIXTURE_TICKET}-a1"
    wt.mkdir(parents=True)
    (wt / "test.py").write_text("pass")

    monkeypatch.setenv("FOREMAN_ROOT", str(tmp_path))
    monkeypatch.setenv("FOREMAN_RUNS_ROOT", str(tmp_path / "_runs"))
    monkeypatch.setenv("FOREMAN_WORKTREES_ROOT", str(worktrees))
    monkeypatch.setenv("FOREMAN_WQ_BIN", "/bin/true")
    monkeypatch.setenv("FOREMAN_SLOCK_BIN", "/bin/true")
    monkeypatch.setenv("AGENT_KILL_INCIDENTS_DIR", str(incidents))
    monkeypatch.setenv("TOKEN_ENGINE_SOCK", "/nonexistent/broker.sock")

    yield {
        "tmp": tmp_path,
        "runs": runs,
        "events": events,
        "incidents": incidents,
        "worktrees": worktrees,
        "wt": wt,
    }

    for d in worktrees.iterdir():
        try:
            d.chmod(0o755)
        except OSError:
            pass
    if os.path.exists(marker):
        os.unlink(marker)


class TestKillSwitch:

    def test_dry_run_changes_nothing(self, env):
        ks = KillSwitch(9999, "test", dry_run=True,
                        incidents_dir=str(env["incidents"]))
        summary = ks.execute()
        assert summary["dry_run"] is True
        assert not os.path.exists(_kill_marker(9999))

    def test_full_kill_creates_marker(self, env):
        ks = KillSwitch(9999, "compromised",
                        incidents_dir=str(env["incidents"]))
        summary = ks.execute()
        assert os.path.exists(_kill_marker(9999))
        with open(_kill_marker(9999)) as f:
            data = json.load(f)
        assert data["ticket_id"] == 9999
        assert data["reason"] == "compromised"
        assert "steps_completed" in data

    def test_all_eight_steps_recorded(self, env):
        ks = KillSwitch(9999, "test",
                        incidents_dir=str(env["incidents"]))
        summary = ks.execute()
        assert len(summary["steps"]) == 8
        expected_steps = ["stop", "revoke", "invalidate_mcp", "cancel",
                          "release", "prevent_restart", "preserve", "quarantine"]
        assert list(summary["steps"].keys()) == expected_steps

    def test_incident_dir_created(self, env):
        ks = KillSwitch(9999, "test",
                        incidents_dir=str(env["incidents"]))
        summary = ks.execute()
        assert summary["incident_dir"] is not None
        assert os.path.isdir(summary["incident_dir"])

    def test_audit_records_preserved(self, env):
        ks = KillSwitch(9999, "test",
                        incidents_dir=str(env["incidents"]))
        summary = ks.execute()
        inc_dir = summary["incident_dir"]
        runs_copy = os.path.join(inc_dir, "runs-wq9999")
        assert os.path.isdir(runs_copy)
        events_copy = os.path.join(inc_dir, "fworker-wq9999.events.jsonl")
        assert os.path.exists(events_copy)

    def test_kill_chain_log_exists(self, env):
        ks = KillSwitch(9999, "test",
                        incidents_dir=str(env["incidents"]))
        summary = ks.execute()
        chain_log = os.path.join(summary["incident_dir"], "kill-log.jsonl")
        assert os.path.exists(chain_log)
        with open(chain_log) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        events = [e["event"] for e in entries]
        assert "kill_initiated" in events
        assert "kill_completed" in events

    def test_idempotent_kill(self, env):
        ks1 = KillSwitch(9999, "first kill",
                         incidents_dir=str(env["incidents"]))
        s1 = ks1.execute()
        ks2 = KillSwitch(9999, "second kill",
                         incidents_dir=str(env["incidents"]))
        s2 = ks2.execute()
        assert s2["steps"]["invalidate_mcp"]["status"] == "already_marked"

    def test_stop_no_pid_is_success(self, env):
        ks = KillSwitch(9999, "test",
                        incidents_dir=str(env["incidents"]))
        result, detail = ks.step1_stop_process()
        assert result == "no_pid_file"

    def test_revoke_no_broker_is_noted(self, env):
        ks = KillSwitch(9999, "test",
                        incidents_dir=str(env["incidents"]))
        ks._init_chain()
        result, detail = ks.step2_revoke_credentials()
        assert result == "broker_not_running"

    def test_quarantine_no_worktrees(self, env, monkeypatch):
        monkeypatch.setenv("FOREMAN_WORKTREES_ROOT", "/nonexistent")
        ks = KillSwitch(9999, "test",
                        incidents_dir=str(env["incidents"]))
        ks._init_chain()
        result, detail = ks.step8_quarantine_workspace()
        assert result == "no_worktrees_found"


class TestUnkill:

    def test_unkill_removes_marker(self, env):
        ks = KillSwitch(9999, "test",
                        incidents_dir=str(env["incidents"]))
        ks.execute()
        assert os.path.exists(_kill_marker(9999))
        results = unkill(9999, "investigation complete")
        assert results["marker"] == "removed"
        assert not os.path.exists(_kill_marker(9999))

    def test_unkill_no_marker(self, env):
        results = unkill(9999, "nothing to undo")
        assert results["marker"] == "not_present"


class TestIsKilled:

    def test_not_killed(self):
        assert is_killed(99999) is None

    def test_killed(self, env):
        ks = KillSwitch(9999, "test",
                        incidents_dir=str(env["incidents"]))
        ks.execute()
        data = is_killed(9999)
        assert data is not None
        assert data["ticket_id"] == 9999
