"""dispatch_health.py classifier changes + dispatch_recover.py ladder (wq#1410,
research/NIGHTLY_DISPATCH_RESILIENCE.md §3.3).

Zero quota, zero live services: dispatcher.log is a hand-written JSONL fixture (same
shape DecisionLog.emit writes), ntfy/wq/systemctl/harness-preflight are fake shell
shims via env-var overrides, and the aq alerts.db is a scratch sqlite file. Every
assertion reads the actor's own transcript (ntfy log, wq calls, state file) rather
than trusting the code read right — same posture as test_dispatcher_health.py.
"""
import json
import os
import stat
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, DISPATCHER_DIR)
import dispatch_health  # noqa: E402
import dispatch_recover  # noqa: E402


def _script(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def fake_preflight(tmp_path, exit_code=0, name="fake-harness-preflight"):
    """_rung_preflight runs [sys.executable, HARNESS_PREFLIGHT_BIN, '--all'] — the
    fake must be a python script, not an arbitrary executable."""
    return _script(tmp_path / name, f"import sys\nsys.exit({exit_code})\n")


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def write_events(log_path, events):
    with open(log_path, "a") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _pid(pid):
    # classify_passes only scores pass_ids shaped like DecisionLog's own
    # "fdisp-<ts>-<pid>" (dispatch_health.py:147) — anything else is ignored.
    return pid if pid.startswith("fdisp-") else f"fdisp-{pid}"


def pass_start(pid, ts=None, dry_run=False):
    return {"ts": ts or _iso(datetime.now(timezone.utc)), "pass_id": _pid(pid),
            "event": "pass_start", "dry_run": dry_run}


def pass_end(pid, ts=None, spawned=0, candidates=0, dispatchable=None):
    ev = {"ts": ts or _iso(datetime.now(timezone.utc)), "pass_id": _pid(pid),
          "event": "pass_end", "spawned": spawned, "candidates": candidates}
    if dispatchable is not None:
        ev["dispatchable"] = dispatchable
    return ev


def error_event(pid, ts=None, stage="config"):
    return {"ts": ts or _iso(datetime.now(timezone.utc)), "pass_id": _pid(pid),
            "event": "error", "stage": stage, "reason": "boom"}


@pytest.fixture
def henv(tmp_path, monkeypatch):
    log_path = tmp_path / "dispatcher.log"
    ntfy_log = tmp_path / "ntfy.jsonl"
    fake_ntfy = _script(tmp_path / "fake-ntfy",
                        "#!/usr/bin/env bash\n"
                        f"echo '---CALL---' >> {ntfy_log}\n"
                        f"echo \"$@\" >> {ntfy_log}\n")
    health_state = tmp_path / "health_state.json"

    monkeypatch.setattr(dispatch_health, "LOG_PATH", str(log_path))
    monkeypatch.setattr(dispatch_health, "STATE_PATH", str(health_state))
    monkeypatch.setattr(dispatch_health, "_LEGACY_STATE_PATH", str(health_state) + ".legacy")
    monkeypatch.setattr(dispatch_health, "NTFY_BIN", fake_ntfy)
    monkeypatch.setattr(dispatch_health, "HUNG_PASS_SECONDS", 600)

    recover_state = tmp_path / "recover_state.json"
    wq_calls = tmp_path / "wq_calls.jsonl"
    fake_wq = _script(tmp_path / "fake-wq",
                      "#!/usr/bin/env bash\n"
                      f"echo '---CALL---' >> {wq_calls}\n"
                      f"echo \"$@\" >> {wq_calls}\n"
                      'echo "added task 4242"\n')
    monkeypatch.setattr(dispatch_recover, "STATE_PATH", str(recover_state))
    monkeypatch.setattr(dispatch_recover, "NTFY_BIN", fake_ntfy)
    monkeypatch.setattr(dispatch_recover, "WQ_BIN", fake_wq)
    monkeypatch.setattr(dispatch_recover, "HARNESS_PREFLIGHT_BIN", fake_preflight(tmp_path))
    monkeypatch.setattr(dispatch_recover, "SYSTEMCTL_BIN", "/bin/true")
    monkeypatch.delenv(dispatch_recover.DISABLE_ENV, raising=False)

    aq_db = tmp_path / "alerts.db"
    conn = sqlite3.connect(aq_db)
    conn.execute("CREATE TABLE alerts(id INTEGER PRIMARY KEY, first_ts TEXT, last_ts TEXT, "
                "source TEXT, key TEXT, title TEXT, message TEXT, priority TEXT, "
                "count INTEGER, status TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(dispatch_recover, "AQ_DB", str(aq_db))

    return {"tmp": tmp_path, "log_path": str(log_path), "ntfy_log": ntfy_log,
           "wq_calls": wq_calls, "aq_db": str(aq_db), "recover_state": recover_state}


def ntfy_lines(henv):
    """One entry per fake-ntfy invocation (messages may embed newlines)."""
    if not henv["ntfy_log"].exists():
        return []
    calls = henv["ntfy_log"].read_text().split("---CALL---\n")
    return [c.replace("\n", " ").strip() for c in calls if c.strip()]


def wq_call_lines(henv):
    """One entry per fake-wq invocation (calls may embed newlines in --detail)."""
    if not henv["wq_calls"].exists():
        return []
    calls = henv["wq_calls"].read_text().split("---CALL---\n")
    return [c for c in calls if c.strip()]


def insert_alert(aq_db, title="[DISPATCH] DISPATCHER STALLED", status="open", count=5):
    c = sqlite3.connect(aq_db)
    c.execute("INSERT INTO alerts(first_ts,last_ts,source,key,title,message,priority,"
             "count,status) VALUES(?,?,?,?,?,?,?,?,?)",
             ("2026-08-23T00:00:00Z", "2026-09-03T00:00:00Z", "ntfy:lumen-alerts",
              title, title, "stalled", "5", count, status))
    c.commit()
    c.close()


def alert_status(aq_db, title="[DISPATCH] DISPATCHER STALLED"):
    c = sqlite3.connect(aq_db)
    row = c.execute("SELECT status FROM alerts WHERE title=?", (title,)).fetchone()
    c.close()
    return row[0] if row else None


# ---- classify_passes: dispatchable-based rule 2 (wq#1410 §3.3.1) --------------------

def test_classify_healthy_when_dispatchable_zero_despite_candidates():
    """A pass with 40 candidates that are all structurally undispatchable must not
    read as a stall — this is exactly the wq#1410 §1.6 ready>0-inflation bug."""
    events = [pass_start("p1"), pass_end("p1", spawned=0, candidates=40, dispatchable=0)]
    results = dispatch_health.classify_passes(events)
    assert results[0][2] == "healthy"


def test_classify_unhealthy_on_spawned_zero_dispatchable_positive():
    events = [pass_start("p1"), pass_end("p1", spawned=0, candidates=5, dispatchable=5)]
    results = dispatch_health.classify_passes(events)
    assert results[0][2] == "unhealthy"
    assert "dispatchable=5" in results[0][3]


def test_classify_falls_back_to_candidates_for_legacy_log_lines():
    """A pass_end with no dispatchable field (pre-wq#1410) behaves exactly as before."""
    events = [pass_start("p1"),
             {"ts": _iso(datetime.now(timezone.utc)), "pass_id": _pid("p1"),
              "event": "pass_end", "spawned": 0, "candidates": 3}]
    results = dispatch_health.classify_passes(events)
    assert results[0][2] == "unhealthy"


# ---- classify_passes: hung pass_start with no pass_end (wq#1410 §3.3.1) -------------

def test_classify_hung_pass_after_timeout():
    old_ts = _iso(datetime.now(timezone.utc) - timedelta(seconds=900))
    events = [pass_start("p1", ts=old_ts)]
    results = dispatch_health.classify_passes(events)
    assert results[0][2] == "unhealthy"
    assert "hung" in results[0][3]


def test_classify_recent_pass_start_not_yet_hung():
    events = [pass_start("p1")]
    results = dispatch_health.classify_passes(events)
    assert results[0][2] == "skip"


def test_classify_only_most_recent_pass_can_be_hung():
    """An older truncated pass_start with no pass_end is a log-tail artifact, not a
    hang — only the latest pass_id in the tail is 'still running'."""
    old_ts = _iso(datetime.now(timezone.utc) - timedelta(seconds=900))
    events = [pass_start("p1", ts=old_ts), pass_start("p2")]
    results = dispatch_health.classify_passes(events)
    by_pid = {pid: v for pid, _ts, v, _r in results}
    assert by_pid[_pid("p1")] == "skip"
    assert by_pid[_pid("p2")] == "skip"


# ---- K=2 not 3 (wq#1410 §3.3.2) ------------------------------------------------------

def test_default_threshold_is_two():
    assert dispatch_health.DEFAULT_THRESHOLD == 2


def test_stall_title_prefixed_and_tagged(henv):
    write_events(henv["log_path"], [
        pass_start("p1"), pass_end("p1", spawned=0, candidates=5, dispatchable=5),
        pass_start("p2"), pass_end("p2", spawned=0, candidates=5, dispatchable=5),
    ])
    fired, _detail = dispatch_health.check_health(henv["log_path"], threshold=2)
    assert fired
    lines = ntfy_lines(henv)
    assert len(lines) == 1
    assert "[DISPATCH] DISPATCHER STALLED" in lines[0]
    assert "rotating_light" in lines[0]


def test_one_unhealthy_pass_below_threshold_does_not_alarm(henv):
    write_events(henv["log_path"], [
        pass_start("p1"), pass_end("p1", spawned=0, candidates=5, dispatchable=5),
    ])
    fired, _detail = dispatch_health.check_health(henv["log_path"], threshold=2)
    assert not fired
    assert ntfy_lines(henv) == []


# ---- dispatch_recover.advance(): the ladder (wq#1410 §3.3.2, Done case (v)) ---------

def test_advance_runs_rung_one_then_two_then_three_then_stops(henv):
    r1 = dispatch_recover.advance()
    assert r1["rung"] == 1 and r1["name"] == "preflight" and r1["ok"] is True
    r2 = dispatch_recover.advance()
    assert r2["rung"] == 2 and r2["name"] == "restart_unit"
    r3 = dispatch_recover.advance()
    assert r3["rung"] == 3 and r3["name"] == "diagnose_config"
    # 4th call: ladder exhausted -> escalate, never a 4th rung
    r4 = dispatch_recover.advance()
    assert r4["escalated"] is True
    assert r4["ticket"] == 4242
    assert len(wq_call_lines(henv)) == 1
    assert "fleet-fires" in wq_call_lines(henv)[0]
    urgent = [l for l in ntfy_lines(henv) if "ladder exhausted" in l]
    assert len(urgent) == 1
    # never loops: a 5th call must not file a second ticket or re-run any rung
    r5 = dispatch_recover.advance()
    assert r5["escalated"] is True
    assert len(wq_call_lines(henv)) == 1


def test_advance_rung_three_names_the_broken_file(henv, tmp_path, monkeypatch):
    """Done case (v), config half: chmod 000 (here: invalid JSON, same effect —
    unreadable/unparseable) config.json -> rung 3 must name it."""
    bad_config = tmp_path / "config.json"
    bad_config.write_text("{not valid json")
    monkeypatch.setattr(dispatch_recover, "CONFIG_PATH", str(bad_config))
    monkeypatch.setattr(dispatch_recover, "PROFILES_DIR", str(tmp_path / "nope"))

    dispatch_recover.advance()  # rung 1
    dispatch_recover.advance()  # rung 2
    r3 = dispatch_recover.advance()  # rung 3
    assert r3["ok"] is False
    assert str(bad_config) in r3["detail"]
    assert "JSON error" in r3["detail"]

    r4 = dispatch_recover.advance()  # exhausted -> fleet-fires ticket
    assert r4["escalated"] is True
    assert len(wq_call_lines(henv)) == 1


def test_reset_sends_recovered_ntfy_and_resolves_alert(henv):
    insert_alert(henv["aq_db"])
    dispatch_recover.advance()  # ladder progresses (rung 1)
    assert not henv["recover_state"].exists() or json.loads(
        henv["recover_state"].read_text())["rung"] == 1

    result = dispatch_recover.reset()
    assert result["had_progress"] is True
    lines = [l for l in ntfy_lines(henv) if "recovered" in l]
    assert len(lines) == 1
    assert alert_status(henv["aq_db"]) == "resolved"
    # ladder state cleared -> the next incident starts at rung 0 again
    assert dispatch_recover.status() == {}


def test_reset_is_silent_when_ladder_never_progressed(henv):
    """A routine healthy pass with no prior incident must not page — only a
    reset that actually recovered something is newsworthy."""
    result = dispatch_recover.reset()
    assert result["had_progress"] is False
    assert ntfy_lines(henv) == []


def test_advance_dry_run_has_zero_side_effects(henv):
    """dry_run must be as side-effect-free as the sensor's own --dry-run: report
    what rung WOULD run next without persisting state, paging, or filing a ticket
    — otherwise a preview call would silently advance a real incident's ladder."""
    r1 = dispatch_recover.advance(dry_run=True)
    assert r1["rung"] == 1 and r1["name"] == "preflight"
    assert not henv["recover_state"].exists()
    assert ntfy_lines(henv) == []
    # a second dry-run call reports the SAME next rung, since nothing persisted
    r2 = dispatch_recover.advance(dry_run=True)
    assert r2["rung"] == 1


def test_recover_disable_env_makes_every_entry_point_a_noop(henv, monkeypatch):
    monkeypatch.setenv(dispatch_recover.DISABLE_ENV, "1")
    assert dispatch_recover.advance() == {"disabled": True}
    assert dispatch_recover.reset() == {"disabled": True}
    assert ntfy_lines(henv) == []
    assert not henv["recover_state"].exists()


def test_advance_never_touches_dispatch_disarm(henv, monkeypatch):
    """The ladder must not read or write any DISPATCH_DISARM-named path/env."""
    monkeypatch.delenv("DISPATCH_DISARM", raising=False)
    dispatch_recover.advance()
    assert "DISPATCH_DISARM" not in os.environ
    # the module docstring documents the guarantee; the code must never act on the
    # name (no os.environ / kill_switch lookup keyed on it).
    import inspect
    src = inspect.getsource(dispatch_recover)
    code_lines = [l for l in src.splitlines() if "DISPATCH_DISARM" in l]
    assert all("os.environ" not in l for l in code_lines)


# ---- end-to-end: dispatch_health.check_health drives the ladder (Done case (v)) ----

def test_check_health_drives_ladder_end_to_end_to_recovery(henv):
    """kill -9 mid-loop shape: two unhealthy passes -> alarm + rung 1 runs; a
    healthy pass afterward resets the ladder and sends 'recovered'."""
    write_events(henv["log_path"], [
        pass_start("p1"), pass_end("p1", spawned=0, candidates=5, dispatchable=5),
        pass_start("p2"), pass_end("p2", spawned=0, candidates=5, dispatchable=5),
    ])
    fired, _ = dispatch_health.check_health(henv["log_path"], threshold=2)
    assert fired
    st = json.loads(henv["recover_state"].read_text())
    assert st["rung"] == 1

    write_events(henv["log_path"], [
        pass_start("p3"), pass_end("p3", spawned=2, candidates=2, dispatchable=2),
    ])
    fired2, _ = dispatch_health.check_health(henv["log_path"], threshold=2)
    assert not fired2
    recovered = [l for l in ntfy_lines(henv) if "recovered" in l]
    assert len(recovered) == 1
    assert dispatch_recover.status() == {}


# ---- selftest (wq#1410 §3.3.5, Done case (vi)) --------------------------------------

def test_selftest_passes_when_stall_and_rung_one_both_behave(henv, capsys):
    rc = dispatch_recover.main(["selftest"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "WOULD ALERT" in out or "correctly WOULD ALERT" in out
    assert "SELFTEST PASS" in out


def test_selftest_fails_loudly_if_classifier_stops_firing(henv, monkeypatch, capsys):
    """selftest exercises the real classifier in dry-run (never the real rung 1
    action — a weekly gate must not actually restart services or reprove repos).
    A regression that stops the alarm from firing on a synthetic stall must fail
    the selftest loudly, not silently."""
    monkeypatch.setattr(dispatch_health, "classify_passes", lambda events: [])
    rc = dispatch_recover.main(["selftest"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "SELFTEST FAIL" in out


def test_cmd_status_zero_side_effects(henv, capsys):
    rc = dispatch_recover.main(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(out) == {"idle": True}
    assert ntfy_lines(henv) == []
