"""dispatch_recover.py — stall recovery ladder unit tests (wq#1410).

Covers research/NIGHTLY_DISPATCH_RESILIENCE.md §3.3 and the test-to-failure plan §5
cases (v) dispatcher crash / config corruption and (vi) watchdog self-test. Zero live
services: systemctl/harness-preflight/ntfy are all faked via monkeypatch, same
convention as test_dispatcher_health.py's henv fixture (fake-ntfy shell shim + tmp
state paths).
"""
import json
import os
import sqlite3
import stat
import sys
from datetime import datetime, timedelta, timezone

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, DISPATCHER_DIR)
import dispatch_health as dh        # noqa: E402
import dispatch_recover as dr       # noqa: E402
import foreman_dispatcher as fd     # noqa: E402


def _script(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    log_path = tmp_path / "dispatcher.log"
    ntfy_log = tmp_path / "ntfy.jsonl"
    fake_ntfy = _script(
        tmp_path / "fake-ntfy",
        f"#!/usr/bin/env bash\necho \"$@\" >> {ntfy_log}\n")

    monkeypatch.setattr(dh, "LOG_PATH", str(log_path))
    monkeypatch.setattr(dh, "STATE_PATH", str(tmp_path / "health_state.json"))
    monkeypatch.setattr(dh, "NTFY_BIN", fake_ntfy)
    monkeypatch.setattr(dh, "ALERT_DEDUP_SECONDS", 0)  # tests fire every tick

    monkeypatch.setattr(dr, "STATE_PATH", str(tmp_path / "recover_state.json"))
    monkeypatch.setattr(dr, "NTFY_BIN", fake_ntfy)
    monkeypatch.setattr(dr, "DISARM_PATH", str(tmp_path / "DISPATCH_DISARM_unused"))

    # Never let a test touch the real fleet alerts.db — every test gets its own
    # scratch db with the same schema aq_escalate.py creates/expects.
    aq_db = tmp_path / "alerts.db"
    conn = sqlite3.connect(str(aq_db))
    conn.executescript("""
        CREATE TABLE alerts(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          first_ts TEXT NOT NULL, last_ts TEXT NOT NULL,
          source TEXT NOT NULL, key TEXT NOT NULL,
          title TEXT, message TEXT, priority TEXT DEFAULT 'default',
          count INTEGER DEFAULT 1, status TEXT DEFAULT 'open',
          owner TEXT DEFAULT 'unassigned', note TEXT, resolved_ts TEXT);
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(dr, "AQ_DB", str(aq_db))

    return {"log_path": str(log_path), "ntfy_log": ntfy_log, "tmp_path": tmp_path,
            "aq_db": str(aq_db)}


def _pass_lines(pass_id, ts, spawned=0, candidates=12, dispatchable=5, extra_end=None):
    lines = [json.dumps({"ts": ts, "pass_id": pass_id, "event": "pass_start",
                         "dry_run": False})]
    end = {"ts": ts, "pass_id": pass_id, "event": "pass_end",
           "candidates": candidates, "spawned": spawned}
    if dispatchable is not None:
        end["dispatchable"] = dispatchable
    if extra_end:
        end.update(extra_end)
    lines.append(json.dumps(end))
    return lines


def write_lines(log_path, lines):
    with open(log_path, "a") as f:
        f.write("\n".join(lines) + "\n")


def iso(dt):
    return dt.isoformat(timespec="seconds")


# ---- dispatchable computation (foreman_dispatcher.py) -----------------------------------

def test_structural_skip_reasons_excluded_from_dispatchable():
    events = [
        {"event": "skip", "reason": "lane_unmapped"},
        {"event": "skip", "reason": "nodispatch_marker"},
        {"event": "skip", "reason": "frank_gated"},
        {"event": "skip", "reason": "deps_unmet"},
        {"event": "skip", "reason": "runaway_stuck"},
        {"event": "skip", "reason": "not_open"},
        {"event": "skip", "reason": "vanished"},
        {"event": "skip", "reason": "fire_unapproved"},
        {"event": "skip", "reason": "fire_no_probe"},
        {"event": "skip", "reason": "lane_cap"},       # NOT structural — real capacity skip
        {"event": "skip", "reason": "governor_deny"},  # NOT structural — budget skip
        {"event": "spawn"},
    ]
    assert fd.count_structural_skips(events) == 9


def test_fire_gate_skips_excluded_from_dispatchable():
    # wq#1423: a backlog entirely of fire tickets stuck on a Frank action
    # (missing approval or missing fire_probe) must not look dispatchable —
    # neither skip is fixable by the dispatcher/recovery ladder, same as frank_gated.
    events = [
        {"event": "skip", "reason": "fire_unapproved"},
        {"event": "skip", "reason": "fire_no_probe"},
        {"event": "skip", "reason": "fire_unapproved"},
    ]
    candidates = len(events)
    dispatchable = candidates - fd.count_structural_skips(events)
    assert dispatchable == 0


def test_dispatchable_zero_when_all_skips_structural():
    events = [{"event": "skip", "reason": r} for r in fd.STRUCTURAL_SKIP_REASONS]
    candidates = len(events)
    dispatchable = candidates - fd.count_structural_skips(events)
    assert dispatchable == 0


# ---- classifier: rule 2 uses dispatchable, not raw candidates ---------------------------

def test_classify_healthy_when_dispatchable_zero_despite_candidates(env):
    now = datetime.now(timezone.utc)
    write_lines(env["log_path"], _pass_lines("fdisp-p1", iso(now), spawned=0,
                                              candidates=50, dispatchable=0))
    results = dh.classify_passes(dh.parse_log_tail(env["log_path"], dh.TAIL_LINES))
    assert results[0][2] == "healthy", results


def test_classify_unhealthy_when_dispatchable_positive(env):
    now = datetime.now(timezone.utc)
    write_lines(env["log_path"], _pass_lines("fdisp-p1", iso(now), spawned=0,
                                              candidates=12, dispatchable=5))
    results = dh.classify_passes(dh.parse_log_tail(env["log_path"], dh.TAIL_LINES))
    assert results[0][2] == "unhealthy", results
    assert "dispatchable=5" in results[0][3]


def test_classify_falls_back_to_candidates_when_dispatchable_missing(env):
    """A pass_end predating this field (no 'dispatchable' key) keeps the old
    candidates>0 behaviour so a stale log tail across a deploy doesn't go blind."""
    now = datetime.now(timezone.utc)
    write_lines(env["log_path"], _pass_lines("fdisp-p1", iso(now), spawned=0,
                                              candidates=7, dispatchable=None))
    results = dh.classify_passes(dh.parse_log_tail(env["log_path"], dh.TAIL_LINES))
    assert results[0][2] == "unhealthy", results


def test_default_threshold_is_two_not_three():
    assert dh.DEFAULT_THRESHOLD == 2


# ---- classifier: hung pass_start with no pass_end past TIMEOUT_START_SEC ----------------

def test_hung_pass_start_no_pass_end_past_timeout_is_unhealthy(env):
    old = datetime.now(timezone.utc) - timedelta(seconds=dh.TIMEOUT_START_SEC + 30)
    write_lines(env["log_path"], [json.dumps({"ts": iso(old), "pass_id": "fdisp-hung",
                                              "event": "pass_start", "dry_run": False})])
    results = dh.classify_passes(dh.parse_log_tail(env["log_path"], dh.TAIL_LINES),
                                 now=datetime.now(timezone.utc))
    assert results[0][2] == "unhealthy"
    assert "hung" in results[0][3]


def test_pass_start_still_within_timeout_is_skip_not_unhealthy(env):
    recent = datetime.now(timezone.utc) - timedelta(seconds=30)
    write_lines(env["log_path"], [json.dumps({"ts": iso(recent), "pass_id": "fdisp-inflight",
                                              "event": "pass_start", "dry_run": False})])
    results = dh.classify_passes(dh.parse_log_tail(env["log_path"], dh.TAIL_LINES),
                                 now=datetime.now(timezone.utc))
    assert results[0][2] == "skip"


# ---- ladder state machine ----------------------------------------------------------------

def _fake_rungs(monkeypatch, ok=True):
    calls = []

    def make(n, name):
        def fn(dry_run):
            calls.append((n, dry_run))
            return ok, f"fake rung {n} ran (dry_run={dry_run})"
        return name, fn

    fake = {1: make(1, "fake-preflight"), 2: make(2, "fake-restart"), 3: make(3, "fake-diagnose")}
    monkeypatch.setattr(dr, "RUNGS", fake)
    return calls


def test_advance_runs_one_rung_per_call_in_order(env, monkeypatch):
    _fake_rungs(monkeypatch)
    r1 = dr.advance(dry_run=False)
    assert r1 == {"action": "ran_rung", "rung": 1, "name": "fake-preflight", "ok": True,
                  "detail": "fake rung 1 ran (dry_run=False)"}
    r2 = dr.advance(dry_run=False)
    assert r2["rung"] == 2 and r2["name"] == "fake-restart"
    r3 = dr.advance(dry_run=False)
    assert r3["rung"] == 3 and r3["name"] == "fake-diagnose"


def test_advance_never_loops_back_after_exhausted(env, monkeypatch):
    calls = _fake_rungs(monkeypatch)
    for _ in range(3):
        dr.advance(dry_run=False)
    exhausted = dr.advance(dry_run=False)
    assert exhausted["action"] == "exhausted"
    assert len(calls) == 3  # rung functions never called a 4th time

    again = dr.advance(dry_run=False)
    assert again["action"] == "stopped"
    assert len(calls) == 3  # still never re-run


def test_exhausted_pages_urgent_with_dispatch_tag(env, monkeypatch):
    _fake_rungs(monkeypatch)
    for _ in range(4):
        dr.advance(dry_run=False)
    transcript = env["ntfy_log"].read_text()
    assert dr.STALL_TAG_TITLE in transcript
    assert "urgent" in transcript


def test_reset_clears_ladder_and_pages_only_if_active(env, monkeypatch):
    _fake_rungs(monkeypatch)
    # No prior incident: reset is silent.
    was_active = dr.reset()
    assert was_active is False
    assert not env["ntfy_log"].exists() or env["ntfy_log"].read_text() == ""

    dr.advance(dry_run=False)  # rung 1
    assert dr.load_state()["rung"] == 1

    was_active = dr.reset()
    assert was_active is True
    assert dr.load_state() == dr._fresh_state()
    assert dr.RECOVERED_TITLE in env["ntfy_log"].read_text()


def _insert_alert(aq_db, title="[DISPATCH] DISPATCHER STALLED", status="open", count=5):
    conn = sqlite3.connect(aq_db)
    conn.execute(
        "INSERT INTO alerts(first_ts,last_ts,source,key,title,message,priority,count,status) "
        "VALUES('t','t','ntfy:lumen-alerts',?,?,?,?,?,?)",
        (title, title, "stalled", "5", count, status))
    conn.commit()
    rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return rid


def test_reset_resolves_open_dispatcher_stalled_alert_rows(env, monkeypatch):
    """wq#1410 §3.3.3: recovery must close the open aq row so aq_escalate's next
    count is per-incident (the row aq#794 lifetime-145 bug this whole ticket exists
    to fix)."""
    _fake_rungs(monkeypatch)
    rid = _insert_alert(env["aq_db"], count=9, status="open")
    dr.advance(dry_run=False)  # ladder becomes active

    dr.reset()

    conn = sqlite3.connect(env["aq_db"])
    row = conn.execute("SELECT status, resolved_ts FROM alerts WHERE id=?", (rid,)).fetchone()
    conn.close()
    assert row[0] == "resolved"
    assert row[1]  # resolved_ts stamped


def test_reset_leaves_unrelated_open_rows_alone(env, monkeypatch):
    _fake_rungs(monkeypatch)
    unrelated = _insert_alert(env["aq_db"], title="probe-pokemon-liveness FAIL",
                              status="open", count=50)
    dr.advance(dry_run=False)
    dr.reset()
    conn = sqlite3.connect(env["aq_db"])
    row = conn.execute("SELECT status FROM alerts WHERE id=?", (unrelated,)).fetchone()
    conn.close()
    assert row[0] == "open"


def test_reset_dry_run_never_touches_the_alert_row(env, monkeypatch):
    _fake_rungs(monkeypatch)
    rid = _insert_alert(env["aq_db"], count=9, status="open")
    dr.advance(dry_run=False)
    dr.reset(dry_run=True)
    conn = sqlite3.connect(env["aq_db"])
    row = conn.execute("SELECT status FROM alerts WHERE id=?", (rid,)).fetchone()
    conn.close()
    assert row[0] == "open"


def test_disable_env_makes_advance_a_noop(env, monkeypatch):
    calls = _fake_rungs(monkeypatch)
    monkeypatch.setenv(dr.DISABLE_ENV, "1")
    result = dr.advance(dry_run=False)
    assert result["action"] == "disabled"
    assert calls == []
    assert dr.load_state()["rung"] == 0


def test_disarm_pauses_ladder_and_is_never_touched(env, monkeypatch, tmp_path):
    disarm = tmp_path / "DISPATCH_DISARM"
    disarm.write_text("frank stopped it")
    monkeypatch.setattr(dr, "DISARM_PATH", str(disarm))
    calls = _fake_rungs(monkeypatch)

    result = dr.advance(dry_run=False)
    assert result["action"] == "paused"
    assert calls == []
    assert disarm.exists()            # never removed
    assert disarm.read_text() == "frank stopped it"  # never modified


# ---- rung 3: names the broken file (§5 case (v), config.json corruption) ----------------

def test_rung3_names_the_broken_json_file(tmp_path, monkeypatch):
    profiles = tmp_path / "permission-profiles"
    repos_d = profiles / "repos.d"
    repos_d.mkdir(parents=True)
    config = tmp_path / "config.json"
    config.write_text("{not valid json")
    (profiles / "base.json").write_text("{}")
    (repos_d / "foreman.json").write_text("{}")

    monkeypatch.setattr(dr, "CONFIG_PATH", str(config))
    monkeypatch.setattr(dr, "PROFILES_DIR", str(profiles))

    ok, detail = dr.rung3_diagnose(dry_run=False)
    assert ok is False
    assert str(config) in detail
    assert "line" in detail.lower() or ":" in detail  # names a location, not just a path


def test_rung3_clean_when_all_json_parses(tmp_path, monkeypatch):
    profiles = tmp_path / "permission-profiles"
    repos_d = profiles / "repos.d"
    repos_d.mkdir(parents=True)
    config = tmp_path / "config.json"
    config.write_text("{}")
    (profiles / "base.json").write_text("{}")
    (repos_d / "foreman.json").write_text("{}")

    monkeypatch.setattr(dr, "CONFIG_PATH", str(config))
    monkeypatch.setattr(dr, "PROFILES_DIR", str(profiles))

    ok, detail = dr.rung3_diagnose(dry_run=False)
    assert ok is True


# ---- §5 case (v): kill -9 mid-loop -> unhealthy after 2 ticks -> ladder -> healthy -------

def test_end_to_end_crash_then_recover_drives_ladder_then_resets(env, monkeypatch):
    calls = _fake_rungs(monkeypatch)
    now = datetime.now(timezone.utc)

    # Two consecutive "kill -9 mid-loop" passes: pass_start with no pass_end, old
    # enough to be past TIMEOUT_START_SEC (the hung-wq-lock / killed-process shape).
    old = now - timedelta(seconds=dh.TIMEOUT_START_SEC + 60)
    write_lines(env["log_path"], [json.dumps({"ts": iso(old), "pass_id": "fdisp-c1",
                                              "event": "pass_start", "dry_run": False})])
    write_lines(env["log_path"], [json.dumps({"ts": iso(old), "pass_id": "fdisp-c2",
                                              "event": "pass_start", "dry_run": False})])

    # Tick 1: alarm condition holds (2 unhealthy) -> rung 1.
    fired, detail = dh.check_health(env["log_path"], threshold=2, dry_run=False)
    assert fired is True
    assert dr.load_state()["rung"] == 1
    assert calls[-1][0] == 1

    # Tick 2 (same log tail, next health cadence): rung 2.
    fired, detail = dh.check_health(env["log_path"], threshold=2, dry_run=False)
    assert dr.load_state()["rung"] == 2
    assert calls[-1][0] == 2

    # Recovery: a healthy pass lands.
    write_lines(env["log_path"], _pass_lines("fdisp-healthy", iso(now), spawned=1,
                                              candidates=3, dispatchable=1))
    fired, detail = dh.check_health(env["log_path"], threshold=2, dry_run=False)
    assert dr.load_state() == dr._fresh_state()
    assert dr.RECOVERED_TITLE in env["ntfy_log"].read_text()


# ---- §5 case (vi): watchdog self-test ----------------------------------------------------

def test_selftest_passes_with_no_side_effects(env, monkeypatch, tmp_path):
    # selftest must not touch the real log/state files this fixture points at.
    monkeypatch.setattr(dr, "STATE_PATH", str(tmp_path / "untouched_recover_state.json"))
    assert dr.selftest() is True
    assert not os.path.exists(str(tmp_path / "untouched_recover_state.json"))


def test_selftest_fails_loudly_if_classifier_stops_detecting_the_stall(env, monkeypatch):
    """Selftest failure of the selftest itself must be observable (page-would-fire
    assertion), not swallowed — simulate a broken classifier and confirm selftest()
    returns False rather than raising or silently passing."""
    monkeypatch.setattr(dh, "classify_passes", lambda events, now=None: [])
    assert dr.selftest() is False
