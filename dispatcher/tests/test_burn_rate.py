"""burn_rate.py — wq#1669 burn-rate spawn controller. The numeric cases mirror
research/DISPATCH_GOVERNOR_WALKTHROUGH.md sec 5 (the 2026-09-10 clamp night)."""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

DISPATCHER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DISPATCHER_DIR)
import burn_rate  # noqa: E402

NOW = datetime(2026, 9, 10, 7, 0, tzinfo=timezone.utc)
CFG = burn_rate.merge_config(None)


@pytest.fixture(autouse=True)
def _frank_inactive_by_default(monkeypatch, tmp_path_factory):
    """wq#1889 added a frank_active(now) read inside allowed_concurrent — every test
    below predates that signal and hard-codes its expected numbers against the plain
    FRANK_RESERVE_PCT reserve. Point WQ_DB at a path that doesn't exist so the REAL
    frank_active() degrades to (False, ...) deterministically, rather than falling
    through to its real default (this BOX's live queue.db), which would make these
    arithmetic tests depend on whether some orch-* session happens to be fresh at
    whatever moment the suite runs. Tests that pass their own db_path, or that exercise
    the bump itself, override this explicitly."""
    monkeypatch.setenv("WQ_DB", str(tmp_path_factory.mktemp("wq1889_default") / "no-such-db"))


# sec 5: session 80% used -> 50% (wq#1889: shifted down by the 30% session reserve so
# (100-pct)-reserve still reproduces the exact 20%-remaining this section's own
# comments and assertions document; the reserve was added in c7cd075, after this
# fixture, against the ORIGINAL pre-reserve percent), resets 07:40Z; weekly_all 83%
# used -> 68% (same shift, 15% weekly reserve, reproduces the documented 17% remaining;
# resets 11:00Z.
SEC5_LIMITS = {
    "session": {"percent": 50, "resets_at": "2026-09-10T07:40:00+00:00",
                "severity": "warning"},
    "weekly_all": {"percent": 68, "resets_at": "2026-09-10T11:00:00+00:00",
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
    # wq#1889: 83 -> 68, same shift as SEC5_LIMITS above (83 - 15% weekly reserve),
    # reproduces the 17%-remaining this test's own comment documents.
    limits = {"weekly_all": {"percent": 68, "resets_at": "2026-09-10T11:00:00+00:00"}}
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
    # wq#1889: 80 -> 50, same shift as SEC5_LIMITS above (80 - 30% session reserve),
    # reproduces the 20%-remaining this test's own comment documents.
    limits = {"session": {"percent": 50, "resets_at": "2026-09-10T11:00:00+00:00"}}
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


# --- wq#1889: frank_active() and the reserve bump it triggers -------------------------

def _agent_sessions_db(tmp_path, rows, name="sessions.db"):
    """Minimal agent_sessions table (wq:332-335): [(base_name, last_seen_str), ...]."""
    path = tmp_path / name
    con = sqlite3.connect(str(path))
    con.execute("""CREATE TABLE agent_sessions(
        session_id TEXT PRIMARY KEY, base_name TEXT NOT NULL, host TEXT, pid INTEGER,
        first_seen TEXT, last_seen TEXT)""")
    for i, (base_name, last_seen) in enumerate(rows):
        con.execute("INSERT INTO agent_sessions VALUES (?,?,?,?,?,?)",
                    (f"{base_name}-{i:06x}", base_name, "lumen", 1000 + i, last_seen, last_seen))
    con.commit()
    con.close()
    return str(path)


def _idle(minutes):
    """A last_seen timestamp `minutes` before NOW, in wq's own storage format (wq:145)."""
    return (NOW - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def test_frank_active_window_edge(tmp_path):
    """Exactly FRANK_ACTIVE_WINDOW_MIN idle still counts (<=, not <); one minute past
    does not."""
    at_edge = _agent_sessions_db(
        tmp_path, [("orch-despag4-3ca53f", _idle(burn_rate.FRANK_ACTIVE_WINDOW_MIN))],
        name="edge.db")
    active, detail = burn_rate.frank_active(NOW, db_path=at_edge)
    assert active is True and "orch-despag4-3ca53f" in detail

    past_edge = _agent_sessions_db(
        tmp_path, [("orch-despag4-3ca53f", _idle(burn_rate.FRANK_ACTIVE_WINDOW_MIN + 1))],
        name="past_edge.db")
    active2, detail2 = burn_rate.frank_active(NOW, db_path=past_edge)
    assert active2 is False and "> window" in detail2


def test_frank_active_ignores_a_stale_orch_row(tmp_path):
    """wq#1883 C.1's live example: an idle=126m orch- row (well past both
    FRANK_ACTIVE_WINDOW_MIN and wq's own 45m WQ_STALE_MIN, but still short of the
    240m purge_stale_sessions reaper — C.2) reads inactive, not an error."""
    db = _agent_sessions_db(tmp_path, [("orch-lpp-triage-eabdb9", _idle(126))])
    active, detail = burn_rate.frank_active(NOW, db_path=db)
    assert active is False
    assert "orch-lpp-triage-eabdb9" in detail and "126.0m" in detail


def test_frank_active_ignores_non_orch_rows(tmp_path):
    db = _agent_sessions_db(tmp_path, [("wq1889-a2", _idle(1)), ("fworker-canary", _idle(0))])
    active, detail = burn_rate.frank_active(NOW, db_path=db)
    assert active is False and "no orch-" in detail


def test_frank_active_picks_the_freshest_of_several_orch_rows(tmp_path):
    db = _agent_sessions_db(tmp_path, [("orch-old-1", _idle(200)), ("orch-fresh-2", _idle(2))])
    active, detail = burn_rate.frank_active(NOW, db_path=db)
    assert active is True and "orch-fresh-2" in detail


def test_frank_active_unreadable_db_degrades_to_inactive_not_a_raise(tmp_path):
    """Unlike admission.py's LIMIT wall, this signal is an enhancement on an
    already-safe floor (wq#1883 C.5), so a broken read must degrade quietly to
    inactive, never raise or fail closed to a refusal."""
    active, detail = burn_rate.frank_active(NOW, db_path=str(tmp_path / "does-not-exist.db"))
    assert active is False and "unreadable" in detail


def test_frank_active_reads_wq_db_env_per_call(tmp_path, monkeypatch):
    """wq#1883 C.3's reasoning applied to this new signal: the db path is resolved per
    call from the WQ_DB env var, not baked in at import, so a path swapped in later
    (here: an env override) takes effect with no restart."""
    db = _agent_sessions_db(tmp_path, [("orch-live", _idle(1))])
    monkeypatch.setenv("WQ_DB", db)
    active, _ = burn_rate.frank_active(NOW)
    assert active is True


def test_reserve_bumps_when_frank_active(monkeypatch):
    """Reserve arithmetic: with Frank active, pool_spend_rate is computed against
    FRANK_RESERVE_PCT_ACTIVE (base reserve + FRANK_RESERVE_PCT_ACTIVE_BUMP points), so
    the same limits and clock as test_mid_session_is_not_clamped_to_one yield a LOWER
    ceiling, not the same one."""
    limits = {"session": {"percent": 50, "resets_at": "2026-09-10T11:00:00+00:00"}}
    inactive, _ = burn_rate.allowed_concurrent(limits, 10, CFG, now=NOW)
    assert inactive == 3  # baseline: (100-50)-30=20% / 4h = 5%/h over 1.5 -> 3.3

    monkeypatch.setattr(burn_rate, "frank_active", lambda now: (True, "orch-x idle=1.0m"))
    active, detail = burn_rate.allowed_concurrent(limits, 10, CFG, now=NOW)
    # (100-50) - (30+15) = 5% remaining / 4h = 1.25%/h over 1.5 -> 0.83 -> min_allowed=1
    assert active == 1
    assert active < inactive
    assert "frank_active=True" in detail


def test_frank_reserve_pct_active_is_the_documented_bump():
    for pool, base in burn_rate.FRANK_RESERVE_PCT.items():
        assert burn_rate.FRANK_RESERVE_PCT_ACTIVE[pool] == pytest.approx(
            base + burn_rate.FRANK_RESERVE_PCT_ACTIVE_BUMP)
