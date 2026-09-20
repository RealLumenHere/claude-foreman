"""wq#2107: a stamp on a disabled timer must alert, not be skipped."""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import cron_deadman  # noqa: E402

US = 1_000_000


def _fake_list_timers(rows):
    def run(*_a, **_k):
        return subprocess.CompletedProcess([], 0, stdout=json.dumps(rows), stderr="")
    return run


def _stamp(tmp_path, unit, age_s):
    p = tmp_path / f".stamp.{unit}"
    p.write_text("")
    t = time.time() - age_s
    os.utime(p, (t, t))


def _run(tmp_path, monkeypatch, rows):
    sent = []
    monkeypatch.setattr(cron_deadman.subprocess, "run", _fake_list_timers(rows))
    monkeypatch.setattr(cron_deadman, "ntfy", lambda pri, title, msg, dry: sent.append(title))
    state = cron_deadman.check(str(tmp_path), str(tmp_path / "state.json"), False)
    return state, sent


def test_disabled_timer_with_old_stamp_alerts(tmp_path, monkeypatch):
    _stamp(tmp_path, "lumen-public-guardian", 7 * 86400)
    rows = [{"unit": "lumen-public-guardian.timer", "activates": "lumen-public-guardian.service",
             "next": None, "last": None}]
    state, sent = _run(tmp_path, monkeypatch, rows)
    assert state["lumen-public-guardian"] == "stale"
    assert sent == ["Dead-man: lumen-public-guardian stale"]


def test_disabled_timer_with_fresh_stamp_is_quiet(tmp_path, monkeypatch):
    _stamp(tmp_path, "x", 60)
    rows = [{"unit": "x.timer", "activates": "x.service", "next": 0, "last": 0}]
    state, sent = _run(tmp_path, monkeypatch, rows)
    assert state["x"] == "ok" and sent == []


def test_armed_timer_keeps_two_x_cadence(tmp_path, monkeypatch):
    _stamp(tmp_path, "y", 500)
    now_us = int(time.time() * US)
    rows = [{"unit": "y.timer", "activates": "y.service",
             "last": now_us - 120 * US, "next": now_us}]
    state, sent = _run(tmp_path, monkeypatch, rows)
    assert state["y"] == "stale" and len(sent) == 1


def test_stamp_without_any_timer_still_skipped(tmp_path, monkeypatch):
    _stamp(tmp_path, "gone", 30 * 86400)
    state, sent = _run(tmp_path, monkeypatch, [])
    assert state == {} and sent == []
