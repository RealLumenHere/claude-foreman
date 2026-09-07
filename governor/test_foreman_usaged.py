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
    floors = usaged.load_floors()
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


def test_daemon_stale_fails_closed_on_missing_updated_at():
    """No updated_at at all (e.g. a hand-edited or half-written state.json)
    must be treated as stale, not as fresh."""
    floors = usaged.load_floors()
    now = dt.datetime.now(dt.timezone.utc)
    assert usaged._daemon_stale({"limits": []}, floors, now) is True
    assert usaged._daemon_stale({}, floors, now) is True
    assert usaged._daemon_stale(None, floors, now) is True


def test_daemon_stale_fails_closed_on_malformed_updated_at():
    """A corrupt updated_at string must not raise and must not be trusted
    as fresh — fail closed like the missing-field case."""
    floors = usaged.load_floors()
    now = dt.datetime.now(dt.timezone.utc)
    assert usaged._daemon_stale({"updated_at": "not-a-timestamp"}, floors, now) is True


def test_restarted_daemon_flips_dispatch_back_to_allow(monkeypatch, tmp_path, capsys):
    """wq#1458 TEST criterion, second half: once the unit restarts and a
    fresh poll writes a recent updated_at, can-dispatch must allow again —
    computed staleness has to clear, not just trip."""
    _isolate(monkeypatch, tmp_path)
    usaged.load_floors()
    healthy_payload = {"limits": [
        {"kind": "session", "percent": 10, "resets_at": "2026-08-16T00:00:00+00:00", "severity": "normal"},
        {"kind": "weekly_all", "percent": 10, "resets_at": "2026-09-10T11:00:00+00:00", "severity": "normal"},
    ]}
    fresh_state = {
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "stale": False,
        "limits": usaged.core.parse_limits(healthy_payload),
    }
    usaged.atomic_write_json(usaged.STATE_PATH, fresh_state)

    rc = usaged.cmd_can_dispatch(argparse.Namespace(model="opus"))

    decision = json.loads(capsys.readouterr().out)
    assert decision["allow"] is True
    assert rc == 0
