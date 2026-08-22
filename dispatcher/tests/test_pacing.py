"""pacing.py — forward-looking weekly-budget pacing unit tests (wq#716 AUTO-4).

Pure-module tests: no subprocess, no filesystem beyond load_config's own config-file
read. Fixed `now` throughout so elapsed-fraction/projection math is exact and
reproducible, not flaky against wall-clock time.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, DISPATCHER_DIR)
import pacing  # noqa: E402

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
WEEK = pacing.WEEK_SECONDS


def resets_in(seconds):
    return (NOW + timedelta(seconds=seconds)).isoformat()


def limits_for(pool, pct, remaining_s):
    return {pool: {"percent": pct, "resets_at": resets_in(remaining_s)}}


def cfg(policy="conservative", **overrides):
    c = json.loads(json.dumps(pacing.DEFAULT_CONFIG))
    c["policy"] = policy
    c.update(overrides)
    return c


# ---- project_pool ----------------------------------------------------------------------

def test_project_pool_missing_data_is_none():
    assert pacing.project_pool({}, "weekly_all", NOW, 0.02) is None
    assert pacing.project_pool({"weekly_all": {"percent": 10}}, "weekly_all", NOW, 0.02) is None


def test_project_pool_too_early_in_week_is_none():
    # 1% of the week elapsed, floor is 2% — noise, not signal
    limits = limits_for("weekly_all", 5, WEEK - int(WEEK * 0.01))
    assert pacing.project_pool(limits, "weekly_all", NOW, 0.02) is None


def test_project_pool_stale_or_rolled_over_is_none():
    limits = limits_for("weekly_all", 50, 0)  # resets_at == now
    assert pacing.project_pool(limits, "weekly_all", NOW, 0.02) is None
    limits = limits_for("weekly_all", 50, -10)  # resets_at in the past
    assert pacing.project_pool(limits, "weekly_all", NOW, 0.02) is None
    limits = limits_for("weekly_all", 50, WEEK * 2)  # bogus far-future horizon
    assert pacing.project_pool(limits, "weekly_all", NOW, 0.02) is None


def test_project_pool_matches_the_wq716_incident():
    # the actual 2026-08-13..08-20 week: ~99% of Fable burned in the first 36h (~21.4% of week)
    remaining_s = WEEK - int(36 * 3600)
    limits = limits_for("fable_weekly_scoped", 99, remaining_s)
    proj = pacing.project_pool(limits, "fable_weekly_scoped", NOW, 0.02)
    assert proj["elapsed_fraction"] == pytest.approx(36 * 3600 / WEEK, rel=1e-6)
    assert proj["projected_pct"] > 400  # wildly over 100% by week end


# ---- assess ------------------------------------------------------------------------------

def test_assess_policy_off_always_ok():
    limits = limits_for("fable_weekly_scoped", 99, WEEK - int(36 * 3600))
    v = pacing.assess(limits, "claude-fable-5", cfg("off"), now=NOW)
    assert v.action == "ok"


def test_assess_fable_pool_only_applies_to_fable_models():
    limits = limits_for("fable_weekly_scoped", 99, WEEK - int(36 * 3600))
    v = pacing.assess(limits, "claude-sonnet-5", cfg(), now=NOW)
    assert v.action == "ok"  # sonnet never draws from the fable-scoped pool
    v = pacing.assess(limits, "claude-fable-5", cfg(), now=NOW)
    assert v.action == "skip"


def test_assess_weekly_all_applies_to_every_model():
    limits = limits_for("weekly_all", 95, WEEK - int(36 * 3600))
    v = pacing.assess(limits, "claude-sonnet-5", cfg(), now=NOW)
    assert v.action in ("delay", "skip")


def test_assess_within_pace_is_ok():
    # 20% of the week elapsed, 15% used — right on pace
    limits = limits_for("weekly_all", 15, WEEK - int(WEEK * 0.20))
    v = pacing.assess(limits, "claude-sonnet-5", cfg(), now=NOW)
    assert v.action == "ok"


def test_assess_worst_pool_wins():
    limits = {
        **limits_for("weekly_all", 20, WEEK - int(WEEK * 0.20)),        # on pace -> ok
        **limits_for("fable_weekly_scoped", 99, WEEK - int(36 * 3600)),  # -> skip
    }
    v = pacing.assess(limits, "claude-fable-5", cfg(), now=NOW)
    assert v.action == "skip"
    assert v.pool == "fable_weekly_scoped"


def test_assess_aggressive_tolerates_more_than_conservative():
    # projects to ~112%: conservative's delay floor (90) catches it, aggressive's (120) doesn't
    limits = limits_for("weekly_all", 56, WEEK - int(WEEK * 0.5))
    conservative = pacing.assess(limits, "claude-sonnet-5", cfg("conservative"), now=NOW)
    aggressive = pacing.assess(limits, "claude-sonnet-5", cfg("aggressive"), now=NOW)
    assert conservative.action == "delay"
    assert aggressive.action == "ok"


# ---- decide (the dispatcher-facing hook) --------------------------------------------------

def test_decide_exempt_ignores_terrible_pace():
    limits = limits_for("fable_weekly_scoped", 99, WEEK - int(36 * 3600))
    v = pacing.decide(pri=5, model="claude-fable-5", limits=limits, config=cfg(),
                      exempt=True, now=NOW)
    assert v.action == "ok"
    assert "exempt" in v.reason


def test_decide_skip_applies_regardless_of_priority():
    limits = limits_for("fable_weekly_scoped", 99, WEEK - int(36 * 3600))
    v = pacing.decide(pri=0, model="claude-fable-5", limits=limits, config=cfg(),
                      exempt=False, now=NOW)
    assert v.action == "skip"


def test_decide_delay_lets_high_pri_through():
    limits = limits_for("weekly_all", 56, WEEK - int(WEEK * 0.5))  # projects ~112% -> delay
    c = cfg("conservative")
    high = pacing.decide(pri=1, model="claude-sonnet-5", limits=limits, config=c,
                         exempt=False, now=NOW)
    low = pacing.decide(pri=2, model="claude-sonnet-5", limits=limits, config=c,
                        exempt=False, now=NOW)
    assert high.action == "ok"
    assert low.action == "delay"


# ---- load_config --------------------------------------------------------------------------

def test_load_config_missing_file_uses_default(tmp_path):
    cfg_val, warn = pacing.load_config(str(tmp_path / "nope.json"))
    assert cfg_val == pacing.DEFAULT_CONFIG
    assert warn is None


def test_load_config_invalid_json_falls_back_with_warning(tmp_path):
    p = tmp_path / "pacing_config.json"
    p.write_text("{not json")
    cfg_val, warn = pacing.load_config(str(p))
    assert cfg_val == pacing.DEFAULT_CONFIG
    assert warn is not None


def test_load_config_bad_policy_falls_back_with_warning(tmp_path):
    p = tmp_path / "pacing_config.json"
    p.write_text(json.dumps({**pacing.DEFAULT_CONFIG, "policy": "yolo"}))
    cfg_val, warn = pacing.load_config(str(p))
    assert cfg_val == pacing.DEFAULT_CONFIG
    assert warn is not None


def test_load_config_valid_custom_loads_as_is(tmp_path):
    custom = json.loads(json.dumps(pacing.DEFAULT_CONFIG))
    custom["policy"] = "aggressive"
    custom["delay_min_pri"] = 3
    p = tmp_path / "pacing_config.json"
    p.write_text(json.dumps(custom))
    cfg_val, warn = pacing.load_config(str(p))
    assert warn is None
    assert cfg_val == custom


def test_shipped_pacing_config_is_valid():
    path = os.path.join(DISPATCHER_DIR, "pacing_config.json")
    cfg_val, warn = pacing.load_config(path)
    assert warn is None
    assert cfg_val["policy"] in pacing.POLICIES
