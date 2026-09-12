"""wq#1812 despag P1-7: account-global Max admission gate. Mocks governor_core's
inputs directly (floors/state/events) rather than touching this box's real
foreman/state.json — the whole point of the gate is to be correct regardless of
what live fleet usage happens to say at test time."""
from __future__ import annotations

import datetime as dt

import pytest

from despag import admission

NOW = dt.datetime(2026, 9, 12, 12, 0, 0, tzinfo=dt.timezone.utc)

DEFAULT_FLOORS = {
    "session_min_remaining_pct": 20,
    "weekly_all_min_remaining_pct": 15,
    "fable_weekly_min_remaining_pct": 50,
    "stale_fail_closed_seconds": 600,
}


@pytest.fixture(autouse=True)
def _reset_admission_globals(monkeypatch):
    """Every test gets a clean slate: no LIMIT wall engaged, no cached governor read
    left over from a previous test (module-level state, since the real gate is a
    process-wide singleton by design — A.4 rule 3 is account-GLOBAL)."""
    monkeypatch.setattr(admission, "_paused_resets_at", None)
    monkeypatch.setattr(admission, "_cached", None)
    monkeypatch.setattr(admission, "_cached_at", 0.0)
    yield


def _mock_state(monkeypatch, *, limits, stale=False, floors=None, stop_active=False,
                 live_events=None, updated_at=NOW):
    floors = floors if floors is not None else dict(DEFAULT_FLOORS)
    state = None if limits is None and stale else {
        "limits": limits, "stale": stale,
        "updated_at": (updated_at.isoformat() if updated_at else None),
    }
    monkeypatch.setattr(admission.foreman_usaged, "load_floors", lambda: floors)
    monkeypatch.setattr(admission.foreman_usaged, "load_state", lambda: state)
    monkeypatch.setattr(admission.foreman_usaged, "load_events", lambda: live_events or {})
    monkeypatch.setattr(admission.foreman_usaged, "STOP_PATH", "/nonexistent/STOP" if not stop_active else __file__)


# --- reserve honoured ----------------------------------------------------------------

def test_reserve_honoured_remaining_below_reserve_refuses(monkeypatch):
    """Session at 75% used (25% remaining) clears the governor's own floor (20%) —
    evaluate_dispatch alone would allow it — but FRANK_RESERVE_PCT reserves 30% of
    the session for Frank's interactive chat (burn_rate.py:54-58), so the spend rate
    above the reserve is 0 and allowed_concurrent clamps to burn_rate.MIN_ALLOWED
    (1): one turn already running must be refused, proving the reserve — not just
    the governor floor — is what's gating."""
    resets_at = (NOW + dt.timedelta(hours=1)).isoformat()
    limits = {
        "session": {"percent": 75, "resets_at": resets_at, "severity": "normal"},
        "weekly_all": {"percent": 10, "resets_at": (NOW + dt.timedelta(days=3)).isoformat(), "severity": "normal"},
    }
    _mock_state(monkeypatch, limits=limits, stale=False)

    first = admission.check_admission("claude-sonnet-5", running_claude_max=0, now=NOW)
    assert first["allow"] is True  # one slot exists (min_allowed=1) even under reserve

    second = admission.check_admission("claude-sonnet-5", running_claude_max=1, now=NOW)
    assert second["allow"] is False
    assert "concurrency ceiling" in second["reasons"][0]


def test_reserve_not_yet_breached_allows_more_than_one(monkeypatch):
    """Sanity check for the test above: plenty of headroom above the reserve permits
    more than the reserve-starved ceiling of 1."""
    resets_at = (NOW + dt.timedelta(hours=1)).isoformat()
    limits = {
        "session": {"percent": 5, "resets_at": resets_at, "severity": "normal"},
        "weekly_all": {"percent": 5, "resets_at": (NOW + dt.timedelta(days=3)).isoformat(), "severity": "normal"},
    }
    _mock_state(monkeypatch, limits=limits, stale=False)
    decision = admission.check_admission("claude-sonnet-5", running_claude_max=2, now=NOW)
    assert decision["allow"] is True


# --- no usable burn-rate data still has a ceiling ---------------------------------------

def test_missing_resets_at_falls_back_to_the_max_concurrent_clamp(monkeypatch):
    """wq#1865 (wq#1864's verify-pass): percent present but resets_at missing on every
    pool claude-sonnet-5 draws on makes burn_rate.allowed_concurrent return
    (None, why) (burn_rate.py:142-143) — the shape a governor poll restart produces.
    The old guard skipped the ceiling entirely on None, so DESPAG_MAX_CLAUDE_CONCURRENT
    was unreachable at any running count. It must clamp instead, exactly like
    foreman_dispatcher.py:1411-1417's tight-mode fallback on the same None."""
    limits = {
        "session": {"percent": 5, "severity": "normal"},
        "weekly_all": {"percent": 5, "severity": "normal"},
    }
    _mock_state(monkeypatch, limits=limits, stale=False)

    below = admission.check_admission(
        "claude-sonnet-5", running_claude_max=admission.DESPAG_MAX_CLAUDE_CONCURRENT - 1, now=NOW)
    assert below["allow"] is True

    at_ceiling = admission.check_admission(
        "claude-sonnet-5", running_claude_max=admission.DESPAG_MAX_CLAUDE_CONCURRENT, now=NOW)
    assert at_ceiling["allow"] is False
    assert "concurrency ceiling" in at_ceiling["reasons"][0]

    way_over = admission.check_admission("claude-sonnet-5", running_claude_max=500, now=NOW)
    assert way_over["allow"] is False


# --- LIMIT wall is account-global -----------------------------------------------------

def test_limit_frame_pauses_a_second_sessions_admission_with_matching_retry_after():
    """A.4 rule 3: a LIMIT observed on session A's frames must refuse session B's
    admission too — the wall is account-global, not per-session — with
    Retry-After == the delta to resets_at, not a guessed constant."""
    resets_at = NOW + dt.timedelta(minutes=37)
    admission.record_limit_frame(resets_at.isoformat())

    decision = admission.check_admission("claude-sonnet-5", running_claude_max=0, now=NOW)
    assert decision["allow"] is False
    assert decision["resets_at"] == resets_at.isoformat()
    assert decision["retry_after_s"] == pytest.approx(37 * 60, abs=1)


def test_limit_wall_clears_once_resets_at_has_passed(monkeypatch):
    resets_at = NOW - dt.timedelta(minutes=1)
    admission.record_limit_frame(resets_at.isoformat())
    _mock_state(monkeypatch, limits={
        "session": {"percent": 5, "resets_at": (NOW + dt.timedelta(hours=1)).isoformat(), "severity": "normal"},
        "weekly_all": {"percent": 5, "resets_at": (NOW + dt.timedelta(days=3)).isoformat(), "severity": "normal"},
    }, stale=False)
    decision = admission.check_admission("claude-sonnet-5", running_claude_max=0, now=NOW)
    assert decision["allow"] is True


def test_limit_observer_detects_a_rejected_rate_limit_event_frame_and_engages_the_wall():
    """The exact wire shape fworker's classifier.RunState reads (classifier.py:201-210)
    — LimitObserver must promote it into the account-global pause without waiting for
    the turn to finish."""
    observer = admission.LimitObserver()
    resets_at = "2026-09-12T13:00:00+00:00"
    observer({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": resets_at}})
    with admission._pause_lock:
        assert admission._paused_resets_at == resets_at


# --- stale governor state fails closed -------------------------------------------------

def test_stale_governor_state_refuses_claude_max(monkeypatch):
    _mock_state(monkeypatch, limits=None, stale=True)
    decision = admission.check_admission("claude-sonnet-5", running_claude_max=0, now=NOW)
    assert decision["allow"] is False


def test_unreadable_governor_state_fails_closed(monkeypatch):
    """Ticket's explicit fail-closed requirement: an exception reading state (not just
    a clean 'stale' flag) must refuse, never crash into an allow."""
    def _raise():
        raise OSError("state.json permission denied")
    monkeypatch.setattr(admission.foreman_usaged, "load_floors", lambda: dict(DEFAULT_FLOORS))
    monkeypatch.setattr(admission.foreman_usaged, "load_state", _raise)
    monkeypatch.setattr(admission.foreman_usaged, "load_events", lambda: {})
    monkeypatch.setattr(admission.foreman_usaged, "STOP_PATH", "/nonexistent/STOP")
    decision = admission.check_admission("claude-sonnet-5", running_claude_max=0, now=NOW)
    assert decision["allow"] is False


def test_evaluate_dispatch_raising_fails_closed(monkeypatch):
    """Even a governor_core internal error (malformed floors, bad state shape) must
    refuse rather than let an unhandled exception fall through to a 500 that some
    caller could mistake for 'no opinion, proceed'."""
    _mock_state(monkeypatch, limits={"session": {"percent": "not-a-number"}}, stale=False)

    def _raise(*a, **k):
        raise ValueError("boom")
    monkeypatch.setattr(admission.governor_core, "evaluate_dispatch", _raise)
    decision = admission.check_admission("claude-sonnet-5", running_claude_max=0, now=NOW)
    assert decision["allow"] is False


# --- litellm-mechanical is never gated -------------------------------------------------

def test_litellm_never_gated_by_stale_state(monkeypatch):
    _mock_state(monkeypatch, limits=None, stale=True)
    decision = admission.check_admission("deepseek-v4-flash-abc", running_claude_max=999, now=NOW)
    assert decision["allow"] is True


def test_litellm_never_gated_by_the_limit_wall():
    admission.record_limit_frame((NOW + dt.timedelta(hours=1)).isoformat())
    decision = admission.check_admission("qwen36-local", running_claude_max=0, now=NOW)
    assert decision["allow"] is True


# --- /v1/uhp lumen.budget extension -----------------------------------------------------

def test_budget_snapshot_reports_reserve_and_paused_until(monkeypatch):
    resets_at = (NOW + dt.timedelta(hours=1)).isoformat()
    _mock_state(monkeypatch, limits={
        "session": {"percent": 40, "resets_at": resets_at, "severity": "normal"},
        "weekly_all": {"percent": 20, "resets_at": resets_at, "severity": "normal"},
    }, stale=False)
    snap = admission.budget_snapshot(now=NOW)
    assert snap["session_pct_used"] == 40
    assert snap["weekly_pct_used"] == 20
    assert snap["reserve_pct"]["session"] == pytest.approx(admission.burn_rate.FRANK_RESERVE_PCT["session"])
    assert snap["paused_until"] is None

    admission.record_limit_frame((NOW + dt.timedelta(minutes=10)).isoformat())
    snap2 = admission.budget_snapshot(now=NOW)
    assert snap2["paused_until"] == (NOW + dt.timedelta(minutes=10)).isoformat()
