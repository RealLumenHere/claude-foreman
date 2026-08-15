"""Unit tests for governor_core.py (wq#613 T1 acceptance: floor logic
unit-tested; synthetic rejected rate_limit_event must block; STOP kill-switch
must halt; stale/no-data must fail-closed to local-only)."""
import datetime as dt

import governor_core as core

NOW = dt.datetime(2026, 8, 15, 18, 0, 0, tzinfo=dt.timezone.utc)  # a Saturday, not off-peak
FLOORS = dict(core.DEFAULT_FLOORS)


def limits_of(session_pct=10, weekly_pct=10, fable_pct=10):
    return {
        "session": {"percent": session_pct, "resets_at": "2026-08-16T00:00:00+00:00", "severity": "normal"},
        "weekly_all": {"percent": weekly_pct, "resets_at": "2026-08-22T00:00:00+00:00", "severity": "normal"},
        "fable_weekly_scoped": {"percent": fable_pct, "resets_at": "2026-08-22T00:00:00+00:00", "severity": "normal"},
    }


# ---------- parse_limits ----------

def test_parse_limits_extracts_three_buckets():
    payload = {
        "limits": [
            {"kind": "session", "percent": 29, "resets_at": "x", "severity": "normal", "scope": None},
            {"kind": "weekly_all", "percent": 52, "resets_at": "x", "severity": "normal", "scope": None},
            {"kind": "weekly_scoped", "percent": 57, "resets_at": "x", "severity": "normal",
             "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}},
        ]
    }
    out = core.parse_limits(payload)
    assert out["session"]["percent"] == 29
    assert out["weekly_all"]["percent"] == 52
    assert out["fable_weekly_scoped"]["percent"] == 57


def test_parse_limits_ignores_non_fable_scoped_buckets():
    payload = {"limits": [
        {"kind": "weekly_scoped", "percent": 10, "resets_at": "x", "severity": "normal",
         "scope": {"model": {"id": None, "display_name": "Opus"}, "surface": None}},
    ]}
    out = core.parse_limits(payload)
    assert "fable_weekly_scoped" not in out


def test_parse_limits_handles_empty_payload():
    assert core.parse_limits({}) == {}


# ---------- floor logic: allow path ----------

def test_allow_when_all_floors_clear():
    d = core.evaluate_dispatch("claude-sonnet-5", limits=limits_of(10, 10, 10), floors=FLOORS,
                                now_utc=NOW, stop_active=False, stale=False)
    assert d["allow"] is True
    assert d["local_only"] is False
    assert d["reasons"] == []


def test_fable_floor_not_checked_for_non_fable_model():
    # Fable bucket deeply breached (90% used) must not block a sonnet dispatch.
    d = core.evaluate_dispatch("claude-sonnet-5", limits=limits_of(10, 10, 90), floors=FLOORS,
                                now_utc=NOW, stop_active=False, stale=False)
    assert d["allow"] is True


# ---------- floor logic: deny path ----------

def test_deny_when_session_floor_breached():
    # session remaining = 100-85 = 15 < 20 floor
    d = core.evaluate_dispatch("claude-sonnet-5", limits=limits_of(85, 10, 10), floors=FLOORS,
                                now_utc=NOW, stop_active=False, stale=False)
    assert d["allow"] is False
    assert d["local_only"] is True
    assert any("session" in r for r in d["reasons"])


def test_deny_when_weekly_all_floor_breached():
    # weekly remaining = 100-90 = 10 < 15 floor
    d = core.evaluate_dispatch("claude-sonnet-5", limits=limits_of(10, 90, 10), floors=FLOORS,
                                now_utc=NOW, stop_active=False, stale=False)
    assert d["allow"] is False
    assert any("weekly_all" in r for r in d["reasons"])


def test_deny_fable_model_when_fable_reserve_breached():
    # fable remaining = 100-55 = 45 < 50 floor, session/weekly otherwise fine
    d = core.evaluate_dispatch("claude-fable-5", limits=limits_of(10, 10, 55), floors=FLOORS,
                                now_utc=NOW, stop_active=False, stale=False)
    assert d["allow"] is False
    assert any("Fable" in r for r in d["reasons"])


def test_fable_model_still_gated_by_session_and_weekly_floors():
    d = core.evaluate_dispatch("claude-fable-5", limits=limits_of(85, 10, 10), floors=FLOORS,
                                now_utc=NOW, stop_active=False, stale=False)
    assert d["allow"] is False


def test_floor_boundary_is_inclusive_of_exact_value():
    # remaining exactly == floor should be allowed (floor is a minimum, not exclusive)
    d = core.evaluate_dispatch("claude-sonnet-5", limits=limits_of(80, 85, 10), floors=FLOORS,
                                now_utc=NOW, stop_active=False, stale=False)
    assert d["allow"] is True


# ---------- STOP kill-switch ----------

def test_stop_active_blocks_regardless_of_healthy_floors():
    d = core.evaluate_dispatch("claude-sonnet-5", limits=limits_of(0, 0, 0), floors=FLOORS,
                                now_utc=NOW, stop_active=True, stale=False)
    assert d["allow"] is False
    assert d["local_only"] is True
    assert "STOP" in d["reasons"][0]


# ---------- fail-closed: stale / missing data ----------

def test_stale_state_fails_closed_to_local_only():
    d = core.evaluate_dispatch("claude-sonnet-5", limits=limits_of(0, 0, 0), floors=FLOORS,
                                now_utc=NOW, stop_active=False, stale=True)
    assert d["allow"] is False
    assert d["local_only"] is True


def test_missing_limits_fails_closed_to_local_only():
    d = core.evaluate_dispatch("claude-sonnet-5", limits=None, floors=FLOORS,
                                now_utc=NOW, stop_active=False, stale=False)
    assert d["allow"] is False
    assert d["local_only"] is True


# ---------- synthetic rejected rate_limit_event (live_blocks) ----------

def test_classify_rejected_frame_is_limit():
    frame = {"status": "rejected", "rateLimitType": "five_hour", "resetsAt": "2026-08-15T20:00:00+00:00"}
    assert core.classify_rate_limit_event(frame) == "LIMIT"


def test_classify_allowed_frame_is_not_limit():
    frame = {"status": "allowed_warning", "rateLimitType": "five_hour"}
    assert core.classify_rate_limit_event(frame) != "LIMIT"


def test_live_rejected_event_blocks_even_with_healthy_floors():
    future = (NOW + dt.timedelta(hours=2)).isoformat()
    live_blocks = {"five_hour": future}
    d = core.evaluate_dispatch("claude-sonnet-5", limits=limits_of(5, 5, 5), floors=FLOORS,
                                now_utc=NOW, stop_active=False, stale=False, live_blocks=live_blocks)
    assert d["allow"] is False
    assert d["local_only"] is True
    assert any("rate_limit_event" in r for r in d["reasons"])


def test_expired_live_block_does_not_block():
    past = (NOW - dt.timedelta(hours=2)).isoformat()
    live_blocks = {"five_hour": past}
    d = core.evaluate_dispatch("claude-sonnet-5", limits=limits_of(5, 5, 5), floors=FLOORS,
                                now_utc=NOW, stop_active=False, stale=False, live_blocks=live_blocks)
    assert d["allow"] is True


# ---------- floor_breaches (for ntfy transition detection) ----------

def test_floor_breaches_lists_only_breached_pools():
    assert core.floor_breaches(limits_of(85, 10, 10), FLOORS) == ["session"]
    assert core.floor_breaches(limits_of(10, 90, 10), FLOORS) == ["weekly_all"]
    assert set(core.floor_breaches(limits_of(85, 90, 55), FLOORS)) == {"session", "weekly_all", "fable_weekly_scoped"}
    assert core.floor_breaches(limits_of(10, 10, 10), FLOORS) == []


def test_floor_breaches_empty_when_limits_none():
    assert core.floor_breaches(None, FLOORS) == []


# ---------- off-peak flag ----------

def test_off_peak_true_on_weekday_morning_pt():
    # 2026-08-14 is a Friday; 13:00 UTC = 05:00 PT
    weekday_morning = dt.datetime(2026, 8, 14, 13, 0, 0, tzinfo=dt.timezone.utc)
    assert core.is_off_peak(weekday_morning, FLOORS) is True


def test_off_peak_false_on_weekend():
    # 2026-08-15 is a Saturday, 13:00 UTC = 05:00 PT
    weekend_morning = dt.datetime(2026, 8, 15, 13, 0, 0, tzinfo=dt.timezone.utc)
    assert core.is_off_peak(weekend_morning, FLOORS) is False


def test_off_peak_false_outside_window():
    weekday_afternoon = dt.datetime(2026, 8, 14, 22, 0, 0, tzinfo=dt.timezone.utc)  # 14:00 PT
    assert core.is_off_peak(weekday_afternoon, FLOORS) is False


def test_is_fable_model_matches_case_insensitively():
    assert core.is_fable_model("claude-fable-5") is True
    assert core.is_fable_model("Claude-Fable-5") is True
    assert core.is_fable_model("claude-sonnet-5") is False
    assert core.is_fable_model(None) is False


# ---------- poll backoff (wq#639: no backoff let a 429 streak self-perpetuate) ----------

def test_poll_backoff_is_base_cadence_when_healthy():
    assert core.poll_backoff_bounds(0, FLOORS) == (60, 90)


def test_poll_backoff_doubles_per_consecutive_failure():
    assert core.poll_backoff_bounds(1, FLOORS) == (120, 180)
    assert core.poll_backoff_bounds(2, FLOORS) == (240, 360)


def test_poll_backoff_caps_at_poll_backoff_max_s():
    lo, hi = core.poll_backoff_bounds(10, FLOORS)
    assert lo == 600 and hi == 600


def test_poll_backoff_respects_custom_floors():
    floors = dict(FLOORS, poll_interval_min_s=10, poll_interval_max_s=20, poll_backoff_max_s=100)
    assert core.poll_backoff_bounds(1, floors) == (20, 40)
    assert core.poll_backoff_bounds(5, floors) == (100, 100)
