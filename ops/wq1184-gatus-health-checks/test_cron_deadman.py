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


def _run_state(tmp_path, unit, status, detail=""):
    p = tmp_path / f".state.{unit}"
    p.write_text(json.dumps({"status": status, "detail": detail}))


def test_fresh_stamp_with_error_run_state_pages_immediately(tmp_path, monkeypatch):
    # wq#2573: a Type=oneshot unit that fails loud never runs ExecStartPost,
    # so the plain stamp stays fresh (touched on a PRIOR success) while the
    # job's own .state.<unit> reports the failure. This must page without
    # waiting on the 2x-cadence staleness threshold.
    _stamp(tmp_path, "lumen-reflector", 60)
    _run_state(tmp_path, "lumen-reflector", "error", "http_401: bad key")
    now_us = int(time.time() * US)
    rows = [{"unit": "lumen-reflector.timer", "activates": "lumen-reflector.service",
             "last": now_us - 7 * 86400 * US, "next": now_us}]
    state, sent = _run(tmp_path, monkeypatch, rows)
    assert state["lumen-reflector"] == "ok"
    assert state["lumen-reflector:run"] == "error"
    assert sent == ["Dead-man: lumen-reflector reported failure"]


def test_error_run_state_pages_once_until_recovered(tmp_path, monkeypatch):
    _stamp(tmp_path, "z", 60)
    _run_state(tmp_path, "z", "error", "boom")
    rows = [{"unit": "z.timer", "activates": "z.service", "next": 0, "last": 0}]
    _, sent1 = _run(tmp_path, monkeypatch, rows)
    assert sent1 == ["Dead-man: z reported failure"]

    # still erroring on the next check: no repeat page (dedup).
    _, sent2 = _run(tmp_path, monkeypatch, rows)
    assert sent2 == []

    # recovers: exactly one recovery notice.
    _run_state(tmp_path, "z", "ok")
    _, sent3 = _run(tmp_path, monkeypatch, rows)
    assert sent3 == ["Dead-man: z run recovered"]
