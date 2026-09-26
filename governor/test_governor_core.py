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


# ---------- T4: local rung governor-exemption ----------

def test_local_model_allowed_despite_all_floors_breached():
    d = core.evaluate_dispatch("deepseek-v4-flash", limits=limits_of(0, 0, 0), floors=FLOORS,
                                now_utc=NOW, stop_active=False, stale=False)
    assert d["allow"] is True
    assert d["local_only"] is False


def test_local_model_allowed_despite_stale_state():
    d = core.evaluate_dispatch("qwen36-local", limits=None, floors=FLOORS,
                                now_utc=NOW, stop_active=False, stale=True)
    assert d["allow"] is True


def test_local_model_allowed_despite_live_rejected_block():
    future = (NOW + dt.timedelta(hours=2)).isoformat()
    live_blocks = {"five_hour": future}
    d = core.evaluate_dispatch("deepseek-v4-flash", limits=limits_of(5, 5, 5), floors=FLOORS,
                                now_utc=NOW, stop_active=False, stale=False, live_blocks=live_blocks)
    assert d["allow"] is True


def test_local_model_still_blocked_by_stop_kill_switch():
    d = core.evaluate_dispatch("deepseek-v4-flash", limits=limits_of(0, 0, 0), floors=FLOORS,
                                now_utc=NOW, stop_active=True, stale=False)
    assert d["allow"] is False
    assert "STOP" in d["reasons"][0]


def test_is_local_model_matches_known_routes_only():
    assert core.is_local_model("deepseek-v4-flash") is True
    assert core.is_local_model("qwen36-local") is True
    assert core.is_local_model("nephthys-qwen3-14b") is False
    assert core.is_local_model("claude-sonnet-5") is False
    assert core.is_local_model(None) is False


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
    assert core.floor_breaches(limits_of(85, 10, 10), FLOORS, NOW) == ["session"]
    assert core.floor_breaches(limits_of(10, 90, 10), FLOORS, NOW) == ["weekly_all"]
    assert set(core.floor_breaches(limits_of(85, 90, 55), FLOORS, NOW)) == {"session", "weekly_all", "fable_weekly_scoped"}
    assert core.floor_breaches(limits_of(10, 10, 10), FLOORS, NOW) == []


def test_floor_breaches_empty_when_limits_none():
    assert core.floor_breaches(None, FLOORS, NOW) == []


# ---------- resolve_fable_floor / fable_floor_schedule (wq#644, Frank's 2026-08-15
# ruling: fable_weekly_scoped reserve steps down on a schedule instead of one static
# number) ----------

FABLE_SCHEDULE = [
    {"until_pt": "2026-08-19T20:30", "min_remaining_pct": 10},
    {"until_pt": "2026-08-20T03:00", "min_remaining_pct": 0},
]


def test_resolve_fable_floor_falls_back_to_static_when_schedule_absent():
    assert core.resolve_fable_floor(FLOORS, NOW) == FLOORS["fable_weekly_min_remaining_pct"]


def test_resolve_fable_floor_falls_back_to_static_when_schedule_empty():
    floors = dict(FLOORS, fable_floor_schedule=[])
    assert core.resolve_fable_floor(floors, NOW) == FLOORS["fable_weekly_min_remaining_pct"]


def test_resolve_fable_floor_picks_first_active_step():
    floors = dict(FLOORS, fable_weekly_min_remaining_pct=50, fable_floor_schedule=FABLE_SCHEDULE)
    # 2026-08-17 12:00 UTC = 2026-08-17 04:00 PT, well before the first step's until_pt.
    before = dt.datetime(2026, 8, 17, 12, 0, 0, tzinfo=dt.timezone.utc)
    assert core.resolve_fable_floor(floors, before) == 10


def test_resolve_fable_floor_step_boundary_crossing():
    floors = dict(FLOORS, fable_weekly_min_remaining_pct=50, fable_floor_schedule=FABLE_SCHEDULE)
    # 2026-08-19T20:30 PT == 2026-08-20T04:30 UTC (PT_OFFSET_HOURS = -8).
    just_before = dt.datetime(2026, 8, 20, 4, 29, 0, tzinfo=dt.timezone.utc)
    at_boundary = dt.datetime(2026, 8, 20, 4, 30, 0, tzinfo=dt.timezone.utc)
    just_after = dt.datetime(2026, 8, 20, 4, 31, 0, tzinfo=dt.timezone.utc)
    assert core.resolve_fable_floor(floors, just_before) == 10
    # at/after the first step's until_pt, that step is no longer active -> second step.
    assert core.resolve_fable_floor(floors, at_boundary) == 0
    assert core.resolve_fable_floor(floors, just_after) == 0


def test_resolve_fable_floor_falls_back_to_static_once_schedule_exhausted():
    floors = dict(FLOORS, fable_weekly_min_remaining_pct=50, fable_floor_schedule=FABLE_SCHEDULE)
    # 2026-08-20T03:00 PT (weekly reset) == 2026-08-20T11:00 UTC — past both steps.
    after_reset = dt.datetime(2026, 8, 20, 11, 1, 0, tzinfo=dt.timezone.utc)
    assert core.resolve_fable_floor(floors, after_reset) == 50


def test_resolve_fable_floor_malformed_schedule_shape_fails_closed_to_static():
    floors = dict(FLOORS, fable_weekly_min_remaining_pct=50, fable_floor_schedule="not-a-list")
    assert core.resolve_fable_floor(floors, NOW) == 50


def test_resolve_fable_floor_malformed_step_fails_closed_to_static():
    # bad until_pt format
    floors = dict(FLOORS, fable_weekly_min_remaining_pct=50,
                  fable_floor_schedule=[{"until_pt": "not-a-date", "min_remaining_pct": 10}])
    assert core.resolve_fable_floor(floors, NOW) == 50
    # missing min_remaining_pct
    floors2 = dict(FLOORS, fable_weekly_min_remaining_pct=50,
                   fable_floor_schedule=[{"until_pt": "2026-08-19T20:30"}])
    assert core.resolve_fable_floor(floors2, NOW) == 50
    # a non-dict entry anywhere in the list
    floors3 = dict(FLOORS, fable_weekly_min_remaining_pct=50,
                   fable_floor_schedule=[{"until_pt": "2026-08-19T20:30", "min_remaining_pct": 10}, "oops"])
    assert core.resolve_fable_floor(floors3, NOW) == 50


def test_evaluate_dispatch_honors_fable_floor_schedule():
    floors = dict(FLOORS, fable_weekly_min_remaining_pct=50, fable_floor_schedule=FABLE_SCHEDULE)
    before = dt.datetime(2026, 8, 17, 12, 0, 0, tzinfo=dt.timezone.utc)
    # remaining=92 (percent=8) clears the scheduled 10% floor but would fail the
    # static 50% floor — proves evaluate_dispatch is reading the schedule, not static.
    d = core.evaluate_dispatch("claude-fable-5", limits=limits_of(10, 10, 8), floors=floors,
                                now_utc=before, stop_active=False, stale=False)
    assert d["allow"] is True

    after_burn_window_start = dt.datetime(2026, 8, 20, 4, 30, 0, tzinfo=dt.timezone.utc)
    # remaining=5 (percent=95) would fail BOTH the static 50% floor and the schedule's
    # own first step (10%) — only advancing to the second step's 0% floor allows this.
    d2 = core.evaluate_dispatch("claude-fable-5", limits=limits_of(10, 10, 95), floors=floors,
                                 now_utc=after_burn_window_start, stop_active=False, stale=False)
    assert d2["allow"] is True


def test_floor_breaches_honors_fable_floor_schedule():
    floors = dict(FLOORS, fable_weekly_min_remaining_pct=50, fable_floor_schedule=FABLE_SCHEDULE)
    # percent=60 -> remaining=40: clears the scheduled 10% floor but not the static 50%.
    before = dt.datetime(2026, 8, 17, 12, 0, 0, tzinfo=dt.timezone.utc)
    assert core.floor_breaches(limits_of(10, 10, 60), floors, before) == []
    after_reset = dt.datetime(2026, 8, 20, 11, 1, 0, tzinfo=dt.timezone.utc)
    # schedule exhausted -> back to the static 50% floor -> 40% remaining breaches.
    assert core.floor_breaches(limits_of(10, 10, 60), floors, after_reset) == ["fable_weekly_scoped"]


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


# ---------- wq#1737: stale usage vs zeroed floors ----------
# floors.json has every floor at 0 (Frank 2026-08-23: only cloud_ceiling limits). A
# reading that cannot deny when fresh must not deny when stale: the stale branch was
# the only thing the usage poll did, and it turned a 12h endpoint 429 streak into a
# fleet-wide dispatch outage. STOP and live_blocks stay hard gates.

ZERO_FLOORS = dict(FLOORS, session_min_remaining_pct=0, weekly_all_min_remaining_pct=0,
                   fable_weekly_min_remaining_pct=0, fable_floor_schedule=[])


def test_zero_floors_and_stale_allows_advisory():
    d = core.evaluate_dispatch("claude-sonnet-5", limits=limits_of(77, 89, 94), floors=ZERO_FLOORS,
                                now_utc=NOW, stop_active=False, stale=True)
    assert d["allow"] is True
    assert d["local_only"] is False
    assert any("advisory" in r and "floors zeroed" in r for r in d["reasons"])


def test_zero_floors_and_missing_limits_allows_advisory():
    d = core.evaluate_dispatch("claude-fable-5", limits=None, floors=ZERO_FLOORS,
                                now_utc=NOW, stop_active=False, stale=True)
    assert d["allow"] is True


def test_nonzero_floors_and_stale_still_denies():
    d = core.evaluate_dispatch("claude-sonnet-5", limits=limits_of(10, 10, 10), floors=FLOORS,
                                now_utc=NOW, stop_active=False, stale=True)
    assert d["allow"] is False
    assert d["local_only"] is True
    assert "stale" in d["reasons"][0]


def test_single_nonzero_applicable_floor_keeps_fail_closed():
    # session+weekly zeroed but a fable reserve is set: a fable model still fails closed
    # on stale data, a non-fable model (fable floor not applicable) does not.
    floors = dict(ZERO_FLOORS, fable_weekly_min_remaining_pct=50)
    fable = core.evaluate_dispatch("claude-fable-5", limits=limits_of(), floors=floors,
                                    now_utc=NOW, stop_active=False, stale=True)
    sonnet = core.evaluate_dispatch("claude-sonnet-5", limits=limits_of(), floors=floors,
                                     now_utc=NOW, stop_active=False, stale=True)
    assert fable["allow"] is False
    assert sonnet["allow"] is True


def test_live_block_and_zero_floors_still_denies():
    d = core.evaluate_dispatch("claude-sonnet-5", limits=limits_of(), floors=ZERO_FLOORS,
                                now_utc=NOW, stop_active=False, stale=True,
                                live_blocks={"five_hour": "2026-08-15T19:00:00+00:00"})
    assert d["allow"] is False
    assert d["local_only"] is True
    assert "live rejected rate_limit_event" in d["reasons"][0]


def test_stop_and_zero_floors_still_denies():
    d = core.evaluate_dispatch("claude-sonnet-5", limits=limits_of(), floors=ZERO_FLOORS,
                                now_utc=NOW, stop_active=True, stale=True)
    assert d["allow"] is False
    assert d["reasons"] == ["STOP kill-switch active"]


def test_fresh_limits_missing_bucket_on_zero_floor_pool_allows():
    # same class: a bucket the poll did not return has no authority when its floor is 0
    limits = {"session": {"percent": 5, "resets_at": "x", "severity": "normal"}}
    d = core.evaluate_dispatch("claude-sonnet-5", limits=limits, floors=ZERO_FLOORS,
                                now_utc=NOW, stop_active=False, stale=False)
    assert d["allow"] is True
    d2 = core.evaluate_dispatch("claude-sonnet-5", limits=limits, floors=FLOORS,
                                 now_utc=NOW, stop_active=False, stale=False)
    assert d2["allow"] is False


def test_floors_zeroed_helper():
    assert core.floors_zeroed("claude-sonnet-5", ZERO_FLOORS, NOW) is True
    assert core.floors_zeroed("claude-fable-5", ZERO_FLOORS, NOW) is True
    assert core.floors_zeroed("claude-sonnet-5", FLOORS, NOW) is False
    sched = dict(ZERO_FLOORS, fable_floor_schedule=[{"until_pt": "2099-01-01T00:00", "min_remaining_pct": 30}])
    assert core.floors_zeroed("claude-fable-5", sched, NOW) is False
    assert core.floors_zeroed("claude-sonnet-5", sched, NOW) is True


# wq#1819: captured wire frame, foreman/_runs/fbuild-625.jsonl:2, status -> rejected
WIRE_REJECTED = {"type": "rate_limit_event",
                 "rate_limit_info": {"status": "rejected", "resetsAt": 1787223600,
                                     "rateLimitType": "seven_day", "utilization": 0.57,
                                     "isUsingOverage": False}}


def test_parse_iso_accepts_epoch_int():
    rt = core._parse_iso(int(NOW.timestamp()) + 3600)
    assert rt is not None and rt > NOW
    assert core._parse_iso(True) is None


def test_classify_nested_wire_frame_and_unwrapped():
    assert core.classify_rate_limit_event(WIRE_REJECTED) == "LIMIT"
    assert core.classify_rate_limit_event(WIRE_REJECTED["rate_limit_info"]) == "LIMIT"
    allowed = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning"}}
    assert core.classify_rate_limit_event(allowed) == "allowed"


def test_evaluate_dispatch_live_block_epoch_int_does_not_raise():
    d = core.evaluate_dispatch("claude-sonnet-5", limits=limits_of(), floors=ZERO_FLOORS,
                               now_utc=NOW, stop_active=False, stale=True,
                               live_blocks={"seven_day": int(NOW.timestamp()) + 3600})
    assert d["allow"] is False
