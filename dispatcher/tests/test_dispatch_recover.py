"""dispatch_recover.py — stall recovery ladder unit tests (wq#1410).

Covers research/NIGHTLY_DISPATCH_RESILIENCE.md §3.3 and the test-to-failure plan §5
cases (v) dispatcher crash / config corruption and (vi) watchdog self-test. Zero live
services: systemctl/harness-preflight/ntfy are all faked via monkeypatch, same
convention as test_dispatcher_health.py's henv fixture (fake-ntfy shell shim + tmp
state paths).
"""
import json
import os
import re
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


# ---- rung 2: restart uses sudo -n, not a bare systemctl (wq#1624) -----------------------

def test_rung2_restart_commands_are_sudo_wrapped(monkeypatch):
    """wq#1624: DISPATCH_TIMER/DISPATCH_SERVICE are SYSTEM units. An unprivileged
    `systemctl restart/start` hits polkit's interactive-auth requirement and always
    fails -- this rung was silently non-functional until sudo -n was added. Assert the
    actual argv, not just the return value, so a regression back to a bare `systemctl`
    call fails this test even if some other environment happens to let it exit 0."""
    seen = []

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return FakeCompleted()

    monkeypatch.setattr(dr.subprocess, "run", fake_run)

    ok, detail = dr.rung2_restart(dry_run=False)

    assert ok is True
    assert len(seen) == 2
    for cmd in seen:
        assert cmd[:2] == ["sudo", "-n"], f"missing sudo -n prefix: {cmd}"
        assert "systemctl" in cmd
    assert any(dr.DISPATCH_TIMER in cmd for cmd in seen)
    assert any(dr.DISPATCH_SERVICE in cmd for cmd in seen)


def test_rung2_restart_dry_run_does_not_call_subprocess(monkeypatch):
    calls = []
    monkeypatch.setattr(dr.subprocess, "run", lambda *a, **k: calls.append(1))

    ok, detail = dr.rung2_restart(dry_run=True)

    assert ok is True
    assert calls == []
    assert "sudo" in detail  # dry-run message should reflect the real command


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


# ---- wq#1411 §4 window gate -------------------------------------------------------------

def _ok(name):
    return {"name": name, "ok": True, "detail": "stub ok", "recoverable": False}


@pytest.fixture
def gate_env(env, monkeypatch, tmp_path):
    """Every check stubbed to pass; individual tests flip one. run_gate never touches
    the real fleet: ntfy -> fake script, report -> tmp, systemd-run -> patched."""
    for name in ("check_breaker", "check_disarm", "check_auth", "check_selftest"):
        monkeypatch.setattr(dr, name, lambda _n=name: _ok(_n))
    monkeypatch.setattr(dr, "check_stamps",
                        lambda fix=True, dry_run=False, budget=None: dict(_ok("stamps"),
                                                                          reproved=[]))
    monkeypatch.setattr(dr, "check_dryrun", lambda dry_run=False, budget=None: _ok("dryrun"))
    monkeypatch.setattr(dr, "check_governor", lambda mode, now=None: _ok("governor"))
    monkeypatch.setattr(dr, "GATE_REPORT_PATH", str(tmp_path / "gate_last.json"))
    monkeypatch.delenv(dr.DISABLE_ENV, raising=False)
    return env


def test_gate_all_pass_sends_one_default_ok_ntfy(gate_env):
    report = dr.run_gate(mode="window")
    assert report["ok"] is True
    lines = gate_env["ntfy_log"].read_text().splitlines()
    assert len(lines) == 1
    assert "-p default" in lines[0] and "[DISPATCH] window gate OK" in lines[0]
    assert json.load(open(dr.GATE_REPORT_PATH))["ok"] is True


def test_gate_nightly_title_differs(gate_env):
    dr.run_gate(mode="nightly")
    assert "[DISPATCH] nightly gate OK" in gate_env["ntfy_log"].read_text()


def test_gate_non_recoverable_failure_pages_urgent_no_rungs(gate_env, monkeypatch):
    monkeypatch.setattr(dr, "check_auth", lambda: {"name": "auth", "ok": False,
                                                    "detail": "loggedIn=False", "recoverable": False})
    ran = []
    monkeypatch.setattr(dr, "_recover_dryrun", lambda dry_run, budget=None: ran.append(1) or [])
    report = dr.run_gate(mode="window")
    assert report["ok"] is False
    text = gate_env["ntfy_log"].read_text()
    pages = [ln for ln in text.splitlines() if ln.startswith("-t lumen-alerts")]
    assert len(pages) == 1 and "-p urgent" in pages[0] and "auth" in pages[0]
    assert "gate FAILED" in pages[0] and "loggedIn=False" in text
    assert ran == []   # auth is not something a rung fixes


def test_gate_dryrun_failure_runs_rungs_then_rechecks(gate_env, monkeypatch):
    calls = []

    def fake_dryrun(dry_run=False, budget=None):
        calls.append("dryrun")
        ok = len(calls) > 1            # first attempt fails, recheck passes
        return {"name": "dryrun", "ok": ok, "recoverable": True,
                "detail": "candidates=5 dispatchable=3 would_spawn=%d" % (2 if ok else 0)}
    monkeypatch.setattr(dr, "check_dryrun", fake_dryrun)
    monkeypatch.setattr(dr, "rung2_restart", lambda dry_run: (True, "restarted (stub)"))
    monkeypatch.setattr(dr, "rung3_diagnose", lambda dry_run: (True, "all json ok (stub)"))
    monkeypatch.setitem(dr.RUNGS, 2, ("restart", dr.rung2_restart))
    monkeypatch.setitem(dr.RUNGS, 3, ("diagnose", dr.rung3_diagnose))
    report = dr.run_gate(mode="nightly")
    assert calls == ["dryrun", "dryrun"]
    assert report["ok"] is True
    assert len(report["recovery"]) == 2
    assert "gate OK" in gate_env["ntfy_log"].read_text()
    # the gate's rungs never touched the health ladder's state
    assert dr.load_state()["rung"] == 0


def test_gate_disable_env_is_report_only(gate_env, monkeypatch):
    monkeypatch.setenv(dr.DISABLE_ENV, "1")
    monkeypatch.setattr(dr, "check_dryrun", lambda dry_run=False, budget=None: {
        "name": "dryrun", "ok": False, "detail": "would_spawn=0", "recoverable": True})
    ran = []
    monkeypatch.setattr(dr, "_recover_dryrun", lambda dry_run, budget=None: ran.append(1) or [])
    report = dr.run_gate(mode="nightly")
    assert report["ok"] is False and report["disabled"] is True and ran == []


def test_gate_dry_run_never_ntfys_and_never_writes_report(gate_env, capsys):
    report = dr.run_gate(mode="window", dry_run=True)
    assert report["ok"] is True
    assert not gate_env["ntfy_log"].exists()
    assert not os.path.exists(dr.GATE_REPORT_PATH)
    assert "WOULD NTFY [default]" in capsys.readouterr().out


def _gov_state(tmp_path, monkeypatch, *, age_s=30, resets_in_s=3600, stale=False, stop=False):
    now = datetime.now(timezone.utc)
    st = {"updated_at": iso(now - timedelta(seconds=age_s)), "stale": stale,
          "stop_active": stop,
          "limits": {"weekly_all": {"percent": 20,
                                    "resets_at": iso(now + timedelta(seconds=resets_in_s))}}}
    p = tmp_path / "state.json"
    p.write_text(json.dumps(st))
    monkeypatch.setattr(dr, "GOVERNOR_STATE", str(p))
    monkeypatch.setattr(dr, "GOVERNOR_FLOORS", str(tmp_path / "no-floors.json"))
    return now


def test_governor_window_passes_when_reset_within_6h(tmp_path, monkeypatch):
    now = _gov_state(tmp_path, monkeypatch, resets_in_s=3 * 3600)
    c = dr.check_governor("window", now=now)
    assert c["ok"], c["detail"]
    assert "resets" in c["detail"]


def test_governor_window_fails_when_reset_too_far_or_past(tmp_path, monkeypatch):
    now = _gov_state(tmp_path, monkeypatch, resets_in_s=8 * 3600)
    assert not dr.check_governor("window", now=now)["ok"]
    now = _gov_state(tmp_path, monkeypatch, resets_in_s=-60)
    assert not dr.check_governor("window", now=now)["ok"]


def test_governor_nightly_ignores_reset_horizon_but_not_staleness(tmp_path, monkeypatch):
    now = _gov_state(tmp_path, monkeypatch, resets_in_s=5 * 86400)
    assert dr.check_governor("nightly", now=now)["ok"]
    # the 2026-08-23 shape: daemon dead, stored stale=False, updated_at 15 days old
    now = _gov_state(tmp_path, monkeypatch, age_s=15 * 86400, resets_in_s=5 * 86400)
    c = dr.check_governor("nightly", now=now)
    assert not c["ok"] and "poll stale" in c["detail"]


def test_governor_unreadable_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "GOVERNOR_STATE", str(tmp_path / "missing.json"))
    assert not dr.check_governor("nightly")["ok"]


def test_disarm_check_reads_never_writes(tmp_path, monkeypatch):
    p = tmp_path / "DISPATCH_DISARM"
    monkeypatch.setattr(dr, "DISARM_PATH", str(p))
    assert dr.check_disarm()["ok"]
    p.write_text("")
    assert not dr.check_disarm()["ok"]
    assert p.exists()


def test_auth_check_parses_logged_in_under_unit_path(monkeypatch):
    seen = {}

    def fake_run(path, argv, timeout=60):
        seen["path"] = path
        seen["argv"] = argv
        return 0, json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                              "subscriptionType": "max"}), ""
    monkeypatch.setattr(dr, "_run_under_unit_env", fake_run)
    monkeypatch.setattr(dr, "_dispatch_unit_path", lambda: ("/a:/b", "from test"))
    c = dr.check_auth()
    assert c["ok"] and "authMethod=claude.ai" in c["detail"]
    assert seen["path"] == "/a:/b" and seen["argv"] == ["claude", "auth", "status"]


def test_auth_check_fails_on_not_found_and_logged_out(monkeypatch):
    monkeypatch.setattr(dr, "_dispatch_unit_path", lambda: ("/nope", "from test"))
    monkeypatch.setattr(dr, "_run_under_unit_env",
                        lambda p, a, timeout=60: (127, "", "sh: 1: claude: not found"))
    c = dr.check_auth()
    assert not c["ok"] and "not found" in c["detail"]
    monkeypatch.setattr(dr, "_run_under_unit_env",
                        lambda p, a, timeout=60: (0, json.dumps({"loggedIn": False}), ""))
    assert not dr.check_auth()["ok"]


def test_summarize_dry_run_shapes():
    ev = lambda **k: k  # noqa: E731
    assert dr._summarize_dry_run([ev(event="pass_end", candidates=9, dispatchable=3, spawned=2)])[0] == "ok"
    assert dr._summarize_dry_run([ev(event="halt", stage="lock", reason="lock_busy")])[0] == "lock_busy"
    assert dr._summarize_dry_run([ev(event="halt", stage="concurrency", reason="no_slots")])[0] == "ok"
    v, d = dr._summarize_dry_run([
        ev(event="skip", stage="lane_policy", reason="repo_mismatch"),
        ev(event="skip", stage="lane_policy", reason="repo_mismatch"),
        ev(event="skip", stage="preflight", reason="no_fresh_stamp"),
        ev(event="pass_end", candidates=3, dispatchable=1, spawned=0)])
    assert v == "fail" and "lane_policy/repo_mismatch=2" in d and "would_spawn=0" in d
    assert dr._summarize_dry_run([ev(event="pass_start")])[0] == "fail"
    assert dr._summarize_dry_run([ev(event="halt", stage="kill_switch", reason="DISARM")])[0] == "fail"
    v, _ = dr._summarize_dry_run([ev(event="pass_end", candidates=0, dispatchable=0, spawned=0)])
    assert v == "fail"


def test_stamps_check_reproves_stale_then_fresh(monkeypatch):
    import types
    calls = {"gate": 0, "reprove": []}

    def gate(repo):
        calls["gate"] += 1
        return (calls["gate"] > 1), (None if calls["gate"] > 1 else "stale preflight stamp")
    fake_hp = types.SimpleNamespace(dispatch_gate_check=gate)
    monkeypatch.setitem(sys.modules, "harness_preflight", fake_hp)
    monkeypatch.setattr(dr, "_migrated_repo_paths", lambda: ["/home/lumen/foreman"])
    monkeypatch.setattr(dr, "_reprove_repo",
                        lambda repo, dry_run, budget=None:
                        calls["reprove"].append(repo) or (True, "rc=0"))
    c = dr.check_stamps()
    assert c["ok"] and c["reproved"] == ["foreman"] and "reproved now" in c["detail"]
    assert calls["reprove"] == ["/home/lumen/foreman"]


def test_stamps_check_no_fix_reports_stale(monkeypatch):
    import types
    fake_hp = types.SimpleNamespace(dispatch_gate_check=lambda r: (False, "no preflight stamp"))
    monkeypatch.setitem(sys.modules, "harness_preflight", fake_hp)
    monkeypatch.setattr(dr, "_migrated_repo_paths", lambda: ["/home/lumen/foreman"])
    c = dr.check_stamps(fix=False)
    assert not c["ok"] and "STALE" in c["detail"]


# ---- wq#1460: the pass has one wall-clock budget ----------------------------------------

class _FakeClock:
    """Monotonic seconds the test moves by hand."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_budget_clamps_a_timeout_to_what_is_left_then_refuses():
    clock = _FakeClock()
    b = dr.GateBudget(1200, clock=clock)
    assert b.timeout_for(330) == 330            # plenty left: the check gets what it asked
    clock.t = 1000
    assert b.timeout_for(330) == 140            # 1200 - 60 reserve - 1000 spent
    clock.t = 1150
    assert b.timeout_for(330) is None and b.exhausted()   # under GATE_MIN_CHECK_S


def test_stamps_stops_reproving_when_the_budget_runs_out(monkeypatch):
    """The wq#1459 CONTRADICT case: N stale repos x 330s each, sequential. The repos past
    the budget are reported STALE, not reproved — the pass never runs out the unit clock."""
    import types
    clock = _FakeClock()
    budget = dr.GateBudget(1200, clock=clock)
    monkeypatch.setitem(sys.modules, "harness_preflight",
                        types.SimpleNamespace(dispatch_gate_check=lambda r: (False, "stale")))
    monkeypatch.setattr(dr, "_migrated_repo_paths",
                        lambda: [f"/home/lumen/repo{i}" for i in range(6)])

    reproved = []

    def slow_reprove(repo, dry_run, budget=None):
        clock.t += budget.timeout_for(dr.GATE_PREFLIGHT_TIMEOUT_S)   # each one burns its slice
        reproved.append(repo)
        return False, "rc=1 timed out proving"
    monkeypatch.setattr(dr, "_reprove_repo", slow_reprove)

    c = dr.check_stamps(budget=budget)
    assert not c["ok"]
    assert len(reproved) == 4                     # 3x330 + one 150s remainder, then stop
    assert c["detail"].count("not reproved — gate budget exhausted") == 2
    assert budget.exhausted()


def test_gate_budget_exhaustion_still_pages_instead_of_running_out_the_unit_clock(
        gate_env, monkeypatch):
    """The failure mode wq#1460 exists to kill: a slow first check must not eat the whole
    TimeoutStartSec, because a SIGTERM'd start job sends neither ntfy and skips
    ExecStartPost (the wq#1184 cron dead-man) — silence that means 'didn't run'."""
    clock = _FakeClock()

    def slow_stamps(fix=True, dry_run=False, budget=None):
        clock.t += 1190                            # a batch of newly-stale stamps
        return dict(_ok("stamps"), reproved=["kb", "mailbot", "nest"])
    monkeypatch.setattr(dr, "check_stamps", slow_stamps)

    report = dr.run_gate(mode="window", budget=dr.GateBudget(1200, clock=clock))
    assert report["ok"] is False
    unrun = [c["name"] for c in report["checks"] if "not run" in c["detail"]]
    assert unrun == ["dryrun", "governor", "breaker", "disarm", "auth", "selftest"]
    assert report["budget"]["exhausted"] is True and report["budget"]["spent_s"] >= 1190
    pages = [ln for ln in gate_env["ntfy_log"].read_text().splitlines()
             if ln.startswith("-t lumen-alerts")]
    assert len(pages) == 1 and "-p urgent" in pages[0] and "gate FAILED" in pages[0]


def test_dryrun_skips_the_lock_busy_retry_sleep_it_cannot_afford(monkeypatch):
    """The retry costs GATE_LOCK_RETRY_S of sleep plus a second pass; with only that much
    budget left, sleeping would put the ntfy past the unit timeout. Report instead."""
    clock = _FakeClock()
    budget = dr.GateBudget(1200, clock=clock)
    clock.t = 1200 - 60 - dr.GATE_LOCK_RETRY_S   # exactly the sleep, nothing for a 2nd pass
    slept = []
    monkeypatch.setattr(dr.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(dr, "_run_dispatcher_dry_run",
                        lambda log, budget=None: (0, "", [{"event": "halt", "stage": "lock",
                                                           "reason": "lock_busy"}]))
    c = dr.check_dryrun(budget=budget)
    assert not c["ok"] and "no retry — gate budget exhausted" in c["detail"]
    assert slept == []


def test_gate_budget_records_spend_on_a_healthy_pass(gate_env):
    report = dr.run_gate(mode="nightly")
    assert report["ok"] is True
    assert report["budget"]["total_s"] == dr.GATE_BUDGET_S
    assert report["budget"]["exhausted"] is False


def test_gate_budget_fits_under_unit_timeout_start_sec():
    """The budget is only a guarantee if the unit outlives it. Read the shipped unit."""
    unit = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(dr.__file__))),
                        "dispatcher", "systemd", "lumen-foreman-gate@.service")
    text = open(unit).read()
    m = re.search(r"^TimeoutStartSec=(\d+)$", text, re.M)
    assert m, "lumen-foreman-gate@.service must set TimeoutStartSec"
    assert dr.GATE_BUDGET_S + dr.GATE_BUDGET_RESERVE_S < int(m.group(1))
    assert "OnFailure=" in text          # a crash still pages (wq#1459)


def test_ladder_rung_timeout_fits_under_health_unit_timeout_start_sec():
    """wq#1966: advance() runs inline inside lumen-foreman-health.service's own
    ExecStart, not the gate's. If a rung's subprocess timeout can outlive that unit's
    TimeoutStartSec, systemd SIGTERMs the health check before it ever reaches _ntfy()
    or lets ExecStartPost touch the wq#1184 dead-man stamp — the watchdog goes stale
    on its own, and cron_deadman pages "Dead-man: ... stale" on every tick instead of
    the real STALLED page ever going out."""
    unit = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(dr.__file__))),
                        "dispatcher", "systemd", "lumen-foreman-health.service")
    text = open(unit).read()
    m = re.search(r"^TimeoutStartSec=(\d+)$", text, re.M)
    assert m, "lumen-foreman-health.service must set TimeoutStartSec"
    assert dr.LADDER_RUNG_TIMEOUT_S + dr.HEALTH_TICK_RESERVE_S <= int(m.group(1))


def test_rung1_preflight_uses_bounded_ladder_timeout(monkeypatch):
    seen = {}

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return FakeCompleted()

    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    ok, detail = dr.rung1_preflight(dry_run=False)

    assert ok is True
    assert seen["timeout"] == dr.LADDER_RUNG_TIMEOUT_S
    assert seen["timeout"] < 580, "must not regress to the old unbounded 580s timeout"


def test_rung2_restart_splits_budget_across_both_calls(monkeypatch):
    seen = []

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen.append(kwargs["timeout"])
        return FakeCompleted()

    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    ok, detail = dr.rung2_restart(dry_run=False)

    assert ok is True
    assert len(seen) == 2
    assert sum(seen) <= dr.LADDER_RUNG_TIMEOUT_S
    assert all(t < 60 for t in seen), "must not regress to the old unbounded 60s-per-call"


def test_health_no_ladder_and_dedup_flags(env, monkeypatch):
    """wq#1411: --no-ladder never advances/resets; --dedup overrides the 1h default."""
    advanced, reset = [], []
    monkeypatch.setattr(dr, "advance", lambda dry_run=False: advanced.append(1))
    monkeypatch.setattr(dr, "reset", lambda dry_run=False, reason="": reset.append(1))
    now = datetime.now(timezone.utc)
    write_lines(env["log_path"], _pass_lines("fdisp-x1", iso(now), spawned=0, dispatchable=4))
    fired, _ = dh.check_health(env["log_path"], threshold=1, ladder=False)
    assert fired and advanced == []
    # regular instance, same state file: suppressed by the window instance's page
    fired2, detail = dh.check_health(env["log_path"], threshold=1, dedup_seconds=1200)
    assert not fired2 and "suppressed" in detail and advanced == [1]
    write_lines(env["log_path"], _pass_lines("fdisp-x2", iso(now), spawned=1, dispatchable=4))
    dh.check_health(env["log_path"], threshold=1, ladder=False)
    assert reset == []
    dh.check_health(env["log_path"], threshold=1)
    assert reset == [1]
