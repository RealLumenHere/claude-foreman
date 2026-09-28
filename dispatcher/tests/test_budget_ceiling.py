"""wq#2426: burn_rate.budget_ceiling — config cloud_ceiling tapered by spendable budget.

The numbers replay the 2026-09-20/21 drain: weekly_all 80% -> 95% at 30-60 cloud
spawns/hour under a `warning` flag the zero-floor governor treats as advisory, then 18
spawns/hour at 95% through the burn-rate controller's min_allowed floor of 1."""
import os
import sys
from datetime import datetime, timezone

import pytest

DISPATCHER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DISPATCHER_DIR)
import burn_rate  # noqa: E402

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
CFG = burn_rate.merge_config(None)
RESET = "2026-09-24T11:00:00+00:00"  # the Thursday weekly reset, ~73h out


@pytest.fixture(autouse=True)
def _frank_inactive_by_default(monkeypatch, tmp_path_factory):
    """Same isolation as test_burn_rate.py: point WQ_DB at a VALID db with an empty
    agent_sessions table so the real frank_active() reads cleanly and answers inactive,
    rather than reading this box's live queue.db. wq#1931: an unreadable db (the old
    "no-such-db" path) now means ACTIVE, which would zero every ceiling asserted here."""
    import sqlite3
    db = tmp_path_factory.mktemp("wq2426") / "empty-sessions.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE agent_sessions(session_id TEXT PRIMARY KEY, base_name TEXT NOT NULL,"
                " host TEXT, pid INTEGER, first_seen TEXT, last_seen TEXT)")
    con.commit()
    con.close()
    monkeypatch.setenv("WQ_DB", str(db))


def _limits(weekly, session=10, fable=None):
    lim = {"session": {"percent": session, "resets_at": "2026-09-21T13:00:00+00:00"},
           "weekly_all": {"percent": weekly, "resets_at": RESET}}
    if fable is not None:
        lim["fable_weekly_scoped"] = {"percent": fable, "resets_at": RESET}
    return lim


def test_plentiful_budget_leaves_the_config_ceiling_alone():
    eff, detail = burn_rate.budget_ceiling(_limits(10), 10, CFG, now=NOW)
    assert eff == 10
    assert "weekly_all: spendable 75.0%" in detail and "-> 10" in detail


def test_ceiling_tapers_linearly_under_the_band():
    # weekly 76%: 24% left, 9% above the 15% reserve, 9/30 of the band -> 3 of 10
    assert burn_rate.budget_ceiling(_limits(76), 10, CFG, now=NOW)[0] == 3
    # 70%: 15% spendable -> 5 of 10
    assert burn_rate.budget_ceiling(_limits(70), 10, CFG, now=NOW)[0] == 5
    # 55%: 30% spendable = the band edge -> untouched
    assert burn_rate.budget_ceiling(_limits(55), 10, CFG, now=NOW)[0] == 10


def test_floor_is_min_allowed_while_any_spendable_budget_remains():
    # weekly 83% (the sec-5 number): 2% spendable -> 10 * 2/30 = 0.67 -> min_allowed 1
    assert burn_rate.budget_ceiling(_limits(83), 10, CFG, now=NOW)[0] == 1
    zero_floor = burn_rate.merge_config({"min_allowed": 0})
    assert burn_rate.budget_ceiling(_limits(83), 10, zero_floor, now=NOW)[0] == 0


def test_at_or_below_the_reserve_is_zero_not_min_allowed():
    """The 2026-09-21 case: weekly 94-95% with three days to the reset. The reserve is
    Frank's, so the ceiling is 0 — not min_allowed, whatever the config floor says."""
    for pct in (85, 94, 95, 100):
        eff, detail = burn_rate.budget_ceiling(_limits(pct), 10, CFG, now=NOW)
        assert eff == 0, pct
        assert "weekly_all: spendable 0.0%" in detail
    assert burn_rate.allowed_concurrent(_limits(94), 10, CFG, now=NOW)[0] == 0


def test_reads_percent_only_no_resets_at_needed():
    limits = {"weekly_all": {"percent": 94}}  # a poll that carried no horizon
    assert burn_rate.allowed_concurrent(limits, 10, CFG, now=NOW)[0] is None
    assert burn_rate.budget_ceiling(limits, 10, CFG, now=NOW)[0] == 0


def test_no_percent_anywhere_keeps_the_config_ceiling():
    for limits in ({}, {"weekly_all": {"resets_at": RESET}}, {"weekly_all": {"percent": None}}):
        assert burn_rate.budget_ceiling(limits, 10, CFG, now=NOW)[0] == 10, limits


def test_session_pool_has_its_own_narrower_band():
    # session reserve 30, band 10: 55% used -> 15% spendable -> untouched; 65% -> 5% ->
    # 10 * 5/10 = 5; 70% -> at the reserve -> 0
    assert burn_rate.budget_ceiling(_limits(10, session=55), 10, CFG, now=NOW)[0] == 10
    assert burn_rate.budget_ceiling(_limits(10, session=65), 10, CFG, now=NOW)[0] == 5
    assert burn_rate.budget_ceiling(_limits(10, session=70), 10, CFG, now=NOW)[0] == 0


def test_fable_pool_narrows_only_when_the_model_draws_on_it():
    limits = _limits(10, fable=92)  # 8% left, under fable's 10% reserve
    assert burn_rate.budget_ceiling(limits, 10, CFG, now=NOW)[0] == 10
    pools = burn_rate.pools_for_model("claude-fable-5-1")
    assert burn_rate.budget_ceiling(limits, 10, CFG, now=NOW, pools=pools)[0] == 0


def test_min_over_pools():
    # session 65% -> 5; weekly 76% -> 3; the tighter pool wins
    assert burn_rate.budget_ceiling(_limits(76, session=65), 10, CFG, now=NOW)[0] == 3


def test_frank_active_widens_the_reserve_and_lowers_the_ceiling(monkeypatch):
    # weekly 70%: 15% spendable inactive -> 5; active bumps the reserve to 30 -> 0
    assert burn_rate.budget_ceiling(_limits(70), 10, CFG, now=NOW)[0] == 5
    monkeypatch.setattr(burn_rate, "frank_active", lambda now, db_path=None: (True, "test"))
    eff, detail = burn_rate.budget_ceiling(_limits(70), 10, CFG, now=NOW)
    assert eff == 0 and detail.startswith("frank_active=True")


def test_config_ceiling_zero_is_cloud_off_and_taper_is_configurable():
    assert burn_rate.budget_ceiling(_limits(10), 0, CFG, now=NOW)[0] == 0
    cfg = burn_rate.merge_config({"ceiling_taper_pct": {"weekly_all": 60}})
    assert cfg["ceiling_taper_pct"]["session"] == 10.0  # untouched pools keep defaults
    # 70%: 15% spendable over a 60-wide band -> 10 * 15/60 = 2
    assert burn_rate.budget_ceiling(_limits(70), 10, cfg, now=NOW)[0] == 2
    bad = burn_rate.merge_config({"ceiling_taper_pct": {"weekly_all": -1, "session": "x"}})
    assert bad["ceiling_taper_pct"] == CFG["ceiling_taper_pct"]  # fail-soft
