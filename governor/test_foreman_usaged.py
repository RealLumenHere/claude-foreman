"""Unit tests for foreman_usaged.py's poll_once() I/O layer (wq#648 S3):
429 handling, cache_age_s, and the "never fail-closed on one stale read"
invariant (RELIABILITY_SPEC.md §B, fworker bug 1).
"""
import argparse
import datetime as dt
import json

import foreman_usaged as usaged

GOOD_PAYLOAD = {"limits": [
    {"kind": "session", "percent": 10, "resets_at": "2026-08-16T00:00:00+00:00", "severity": "normal"},
]}


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(usaged, "STOP_PATH", str(tmp_path / "STOP"))
    monkeypatch.setattr(usaged, "EVENTS_PATH", str(tmp_path / "events.json"))
    monkeypatch.setattr(usaged, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(usaged, "send_ntfy", lambda *a, **k: None)


def test_first_good_poll_has_zero_cache_age(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(usaged, "fetch_primary", lambda: (True, GOOD_PAYLOAD, None))
    state = usaged.poll_once(usaged.load_floors(), None)
    assert state["stale"] is False
    assert state["cache_age_s"] == 0.0
    assert state["reused_cache"] is False


def test_single_429_reuses_cache_and_does_not_fail_closed(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    floors = usaged.load_floors()

    monkeypatch.setattr(usaged, "fetch_primary", lambda: (True, GOOD_PAYLOAD, None))
    prev = usaged.poll_once(floors, None)

    monkeypatch.setattr(usaged, "fetch_primary", lambda: (False, None, "HTTP 429"))
    monkeypatch.setattr(usaged, "fetch_ccc", lambda: (False, None, "no cookie"))
    state = usaged.poll_once(floors, prev)

    assert state["stale"] is False, "one bad read must never fail-closed"
    assert state["limits"] == prev["limits"], "429 must reuse the last-good cache"
    assert state["reused_cache"] is True
    assert state["reused_cache_reason"] == "HTTP 429"
    assert state["cache_age_s"] is not None and state["cache_age_s"] >= 0
    assert state["primary_fail_streak"] == 1


def test_repeated_429s_past_threshold_do_fail_closed(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    floors = dict(usaged.load_floors())
    floors["stale_fail_closed_seconds"] = 5

    old_last_good = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=999)).isoformat()
    cached_limits = usaged.core.parse_limits(GOOD_PAYLOAD)
    prev = {
        "last_good_at": old_last_good, "limits": cached_limits,
        "primary_fail_streak": 3, "floor_breaches": [], "stale": False,
    }
    monkeypatch.setattr(usaged, "fetch_primary", lambda: (False, None, "HTTP 429"))
    monkeypatch.setattr(usaged, "fetch_ccc", lambda: (False, None, "no cookie"))
    state = usaged.poll_once(floors, prev)

    assert state["stale"] is True
    assert state["cache_age_s"] > floors["stale_fail_closed_seconds"]
    assert state["limits"] == cached_limits, "still serves the cache, just flagged stale"


def test_non_429_failure_also_reuses_cache_without_the_429_label(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    floors = usaged.load_floors()
    monkeypatch.setattr(usaged, "fetch_primary", lambda: (True, GOOD_PAYLOAD, None))
    prev = usaged.poll_once(floors, None)

    monkeypatch.setattr(usaged, "fetch_primary", lambda: (False, None, "HTTP 503"))
    monkeypatch.setattr(usaged, "fetch_ccc", lambda: (False, None, "no cookie"))
    state = usaged.poll_once(floors, prev)

    assert state["reused_cache"] is True
    assert state["reused_cache_reason"] == "HTTP 503"
    assert state["stale"] is False


def test_dead_daemon_computed_staleness_denies_despite_stored_stale_false(monkeypatch, tmp_path, capsys):
    """wq#1458: a dead daemon never runs poll_once again, so the last-written
    `stale` flag freezes at whatever it was the moment the daemon died —
    typically False. can-dispatch must not trust that frozen flag forever;
    it has to recompute staleness from state.json's own updated_at age.
    """
    _isolate(monkeypatch, tmp_path)
    # pin non-zero floors: this test is about computed staleness, not the live
    # floors.json (all 0 since 2026-08-23, which makes stale advisory — wq#1737)
    monkeypatch.setattr(usaged, "load_floors", lambda: dict(usaged.core.DEFAULT_FLOORS))
    long_dead = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=15)).isoformat()
    stale_state = {
        "updated_at": long_dead,
        "stale": False,
        "limits": usaged.core.parse_limits(GOOD_PAYLOAD),
    }
    usaged.atomic_write_json(usaged.STATE_PATH, stale_state)

    rc = usaged.cmd_can_dispatch(argparse.Namespace(model="opus"))

    decision = json.loads(capsys.readouterr().out)
    assert decision["allow"] is False
    assert decision["local_only"] is True
    assert rc == 1


def test_can_dispatch_stale_state_allows_when_floors_zeroed(monkeypatch, tmp_path, capsys):
    """wq#1737: same dead-daemon state, floors all 0 -> allow, advisory reason."""
    _isolate(monkeypatch, tmp_path)
    zero = dict(usaged.core.DEFAULT_FLOORS, session_min_remaining_pct=0,
                weekly_all_min_remaining_pct=0, fable_weekly_min_remaining_pct=0)
    monkeypatch.setattr(usaged, "load_floors", lambda: zero)
    long_dead = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=15)).isoformat()
    usaged.atomic_write_json(usaged.STATE_PATH, {"updated_at": long_dead, "stale": True,
                                                "limits": usaged.core.parse_limits(GOOD_PAYLOAD)})
    rc = usaged.cmd_can_dispatch(argparse.Namespace(model="claude-fable-5"))
    decision = json.loads(capsys.readouterr().out)
    assert decision["allow"] is True and rc == 0
    assert "advisory" in decision["reasons"][0]
    # STOP still wins
    open(usaged.STOP_PATH, "w").close()
    rc = usaged.cmd_can_dispatch(argparse.Namespace(model="claude-fable-5"))
    assert json.loads(capsys.readouterr().out)["allow"] is False and rc == 1


# ---------- wq#1737: aq row on the stale edge, resolved on recovery ----------

def _aq_spy(monkeypatch):
    calls = []
    monkeypatch.setattr(usaged, "_aq", lambda *argv: calls.append(argv) or True)
    return calls


def test_stale_edge_opens_aq_row_and_recovery_resolves_it(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    calls = _aq_spy(monkeypatch)
    open_ids = [None]
    monkeypatch.setattr(usaged, "aq_open_id", lambda: open_ids[0])
    floors = dict(usaged.core.DEFAULT_FLOORS, session_min_remaining_pct=0,
                  weekly_all_min_remaining_pct=0, fable_weekly_min_remaining_pct=0)
    monkeypatch.setattr(usaged, "fetch_primary", lambda: (True, GOOD_PAYLOAD, None))
    st = usaged.poll_once(floors, None)
    assert calls == []
    # age the good read past the threshold, then fail the poll
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=700)
    st["last_good_at"] = old.isoformat()
    monkeypatch.setattr(usaged, "fetch_primary", lambda: (False, None, "HTTP 429"))
    monkeypatch.setattr(usaged, "fetch_ccc", lambda: (False, None, "no cookie"))
    st = usaged.poll_once(floors, st)
    assert st["stale"] is True
    assert len(calls) == 1 and calls[0][:3] == ("add", "foreman-governor", "usage-stale")
    assert "advisory" in calls[0][4]
    # a later stale poll with the row still open does not re-add
    open_ids[0] = 42
    st = usaged.poll_once(floors, st)
    assert len(calls) == 1
    # row resolved by hand while still stale -> re-add
    open_ids[0] = None
    st = usaged.poll_once(floors, st)
    assert len(calls) == 2 and calls[1][0] == "add"
    # recovery resolves whatever open row exists
    open_ids[0] = 43
    monkeypatch.setattr(usaged, "fetch_primary", lambda: (True, GOOD_PAYLOAD, None))
    st = usaged.poll_once(floors, st)
    assert st["stale"] is False
    assert calls[-1][:2] == ("resolve", "43")


def test_stale_edge_message_says_fail_closed_when_a_floor_is_set(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    calls = _aq_spy(monkeypatch)
    monkeypatch.setattr(usaged, "aq_open_id", lambda: None)
    floors = dict(usaged.core.DEFAULT_FLOORS)
    monkeypatch.setattr(usaged, "fetch_primary", lambda: (True, GOOD_PAYLOAD, None))
    st = usaged.poll_once(floors, None)
    st["last_good_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=700)).isoformat()
    monkeypatch.setattr(usaged, "fetch_primary", lambda: (False, None, "HTTP 429"))
    monkeypatch.setattr(usaged, "fetch_ccc", lambda: (False, None, "no cookie"))
    usaged.poll_once(floors, st)
    assert "FAIL-CLOSED" in calls[0][4]


def test_aq_open_id_reads_scratch_db(monkeypatch, tmp_path):
    import sqlite3
    db = tmp_path / "alerts.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE alerts(id INTEGER PRIMARY KEY, source TEXT, key TEXT, status TEXT)")
    c.execute("INSERT INTO alerts VALUES(7,'foreman-governor','usage-stale','resolved')")
    c.execute("INSERT INTO alerts VALUES(9,'foreman-governor','usage-stale','open')")
    c.commit(); c.close()
    monkeypatch.setattr(usaged, "AQ_DB", str(db))
    assert usaged.aq_open_id() == 9
    monkeypatch.setattr(usaged, "AQ_DB", str(tmp_path / "missing.db"))
    assert usaged.aq_open_id() is None
