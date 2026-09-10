"""burn_rate.py — wq#1669 burn-rate spawn controller. The numeric cases mirror
research/DISPATCH_GOVERNOR_WALKTHROUGH.md sec 5 (the 2026-09-10 clamp night)."""
import json
import os
import sys
from datetime import datetime, timezone

DISPATCHER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DISPATCHER_DIR)
import burn_rate  # noqa: E402

NOW = datetime(2026, 9, 10, 7, 0, tzinfo=timezone.utc)
CFG = burn_rate.merge_config(None)

# sec 5: session 80% (resets 07:40Z), weekly_all 83% (resets 11:00Z)
SEC5_LIMITS = {
    "session": {"percent": 80, "resets_at": "2026-09-10T07:40:00+00:00",
                "severity": "warning"},
    "weekly_all": {"percent": 83, "resets_at": "2026-09-10T11:00:00+00:00",
                   "severity": "warning"},
}


def test_sec5_case_opens_past_the_old_tight_clamp():
    """The night of 2026-09-10 the binary clamp allowed 1 spawn/pass with 3 units live.
    The controller reads the same numbers as an expiring budget and allows more."""
    allowed, detail = burn_rate.allowed_concurrent(SEC5_LIMITS, 10, CFG, now=NOW)
    # wq#1699 measured rates: session 20%/0.667h = 30%/h over 1.5%/worker-h -> 20;
    # weekly 17%/4h = 4.25%/h over 0.35 -> 12.1; min 12.1 clamped to the ceiling 10
    assert allowed == 10
    n, detail = burn_rate.allowed_this_pass(SEC5_LIMITS, 3, 10, CFG, now=NOW)
    assert n == 7 and "running=3" in detail  # vs the old tight cap of 1


def test_cap_grows_toward_ceiling_as_reset_approaches():
    limits = {"weekly_all": {"percent": 83, "resets_at": "2026-09-10T11:00:00+00:00"}}
    allowed, _ = burn_rate.allowed_concurrent(limits, 20, CFG, now=NOW)
    assert allowed == 12  # 17% left / 4h = 4.25%/h over 0.35 -> 12.1 -> 12
    allowed, _ = burn_rate.allowed_concurrent(limits, 10, CFG, now=NOW)
    assert allowed == 10  # never past cloud_ceiling


def test_cap_shrinks_when_projected_burn_exceeds_remaining():
    # same 17% remaining but 100h to the reset: 0.17%/h over 0.35 -> min_allowed
    limits = {"weekly_all": {"percent": 83, "resets_at": "2026-09-14T11:00:00+00:00"}}
    allowed, _ = burn_rate.allowed_concurrent(limits, 10, CFG, now=NOW)
    assert allowed == CFG["min_allowed"] == 1


def test_near_empty_near_reset_still_allows_min():
    limits = {"weekly_all": {"percent": 98, "resets_at": "2026-09-10T11:00:00+00:00"}}
    allowed, _ = burn_rate.allowed_concurrent(limits, 10, CFG, now=NOW)
    assert allowed == 1  # the quota expires anyway


def test_no_reliable_data_returns_none():
    for limits in ({}, {"session": {"percent": 80}},                      # no resets_at
                   {"session": {"resets_at": "2026-09-10T07:40:00+00:00"}},  # no percent
                   {"session": {"percent": 80, "resets_at": "bogus"}},
                   {"session": {"percent": 80,                            # rolled over
                                "resets_at": "2026-09-10T06:00:00+00:00"}},
                   {"session": {"percent": 80,                            # bogus horizon
                                "resets_at": "2026-09-12T07:00:00+00:00"}}):
        allowed, _ = burn_rate.allowed_concurrent(limits, 10, CFG, now=NOW)
        assert allowed is None, limits
    n, _ = burn_rate.allowed_this_pass({}, 3, 10, CFG, now=NOW)
    assert n is None


def test_min_over_pools_and_running_subtraction():
    allowed, _ = burn_rate.allowed_concurrent(SEC5_LIMITS, 10, CFG, now=NOW)
    n, _ = burn_rate.allowed_this_pass(SEC5_LIMITS, allowed, 10, CFG, now=NOW)
    assert n == 0  # already at the supported concurrency
    n, _ = burn_rate.allowed_this_pass(SEC5_LIMITS, allowed + 5, 10, CFG, now=NOW)
    assert n == 0  # never negative


def test_observed_rates_override_config_estimates():
    limits = {"weekly_all": {"percent": 83, "resets_at": "2026-09-10T11:00:00+00:00"}}
    allowed, detail = burn_rate.allowed_concurrent(limits, 10, CFG,
                                                   observed={"weekly_all": 4.25}, now=NOW)
    assert allowed == 1  # 4.25%/h / 4.25%/worker-h = 1 concurrent


def test_merge_config_fail_soft():
    assert burn_rate.merge_config("not a dict") == CFG
    c = burn_rate.merge_config({"avg_worker_pct_per_hour": {"session": 10, "weekly_all": -1},
                                "min_allowed": True})
    assert c["avg_worker_pct_per_hour"]["session"] == 10.0
    assert c["avg_worker_pct_per_hour"]["weekly_all"] == 0.35  # invalid keeps default
    assert c["min_allowed"] == 1  # bool is not an int here
    c2 = burn_rate.merge_config({"min_allowed": 2})
    assert c2["min_allowed"] == 2


def test_load_observed(tmp_path):
    assert burn_rate.load_observed(str(tmp_path / "absent.json")) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    assert burn_rate.load_observed(str(bad)) == {}
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps({"weekly_all": 0.7, "session": -2, "junk": "x"}))
    assert burn_rate.load_observed(str(ok)) == {"weekly_all": 0.7}


def test_mid_session_is_not_clamped_to_one():
    """wq#1699 review of the wq#1669 estimates: session 5%/worker-h turned a 20%-left
    session with 4h to go into 1 concurrent (0 spawns with 3 running) — the same wrong
    clamp in a new place. Measured 1.24 (rounded to 1.5) supports 3."""
    limits = {"session": {"percent": 80, "resets_at": "2026-09-10T11:00:00+00:00"}}
    allowed, _ = burn_rate.allowed_concurrent(limits, 10, CFG, now=NOW)
    assert allowed == 3  # 20%/4h = 5%/h over 1.5 -> 3.3


def test_pools_for_model_mirrors_router():
    assert burn_rate.pools_for_model("claude-sonnet-5") == ("session", "weekly_all")
    assert burn_rate.pools_for_model("claude-fable-5") == (
        "session", "weekly_all", "fable_weekly_scoped")
    assert burn_rate.pools_for_model(None) == ("session", "weekly_all")


def test_fable_pool_narrows_only_the_fable_ceiling():
    limits = dict(SEC5_LIMITS)
    limits["fable_weekly_scoped"] = {"percent": 99, "resets_at": "2026-09-14T11:00:00+00:00",
                                     "severity": "critical"}
    sonnet, _ = burn_rate.allowed_concurrent(
        limits, 10, CFG, now=NOW, pools=burn_rate.pools_for_model("claude-sonnet-5"))
    fable, detail = burn_rate.allowed_concurrent(
        limits, 10, CFG, now=NOW, pools=burn_rate.pools_for_model("claude-fable-5"))
    assert sonnet == 10
    assert fable == 1 and "fable_weekly_scoped" in detail
