"""Classifier unit tests — CLAUDE_CLI_INTERNALS.md §E / BUILD_PLAN.md §T2a adversarial
verify item 1. Zero quota: canned stream-json fixtures, no claude invocation.
The ticket's named requirement: 'a synthetic rejected-limit frame classifies LIMIT not
CRASH' — see test_rejected_rate_limit_event_is_limit_not_crash below.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from classifier import RunState, classify, SUCCESS, REFUSED, TURNS, BUDGET, LIMIT, CRASH, HANG


def run(frames, hung=False, reaped=False):
    st = RunState()
    for f in frames:
        st.observe(f)
    st.hung = hung
    st.reaped = reaped
    return classify(st)


def test_clean_success():
    frames = [
        {"type": "system", "subtype": "init", "session_id": "s1", "model": "claude-sonnet-5"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}},
        {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01},
    ]
    assert run(frames) == SUCCESS


def test_rejected_rate_limit_event_is_limit_not_crash():
    # the ticket's explicit adversarial requirement
    frames = [
        {"type": "system", "subtype": "init", "session_id": "s1"},
        {"type": "rate_limit_event", "status": "rejected", "rateLimitType": "five_hour",
         "resetsAt": 1234567890, "utilization": 1.0},
        {"type": "result", "subtype": "error_during_execution", "is_error": True,
         "errors": ["usage limit reached"]},
    ]
    st = RunState()
    for f in frames:
        st.observe(f)
    assert st.saw_limit is True
    assert st.latest_resets_at == 1234567890
    assert classify(st) == LIMIT
    assert classify(st) != CRASH


def test_api_error_rate_limits_nonnull_is_limit():
    frames = [
        {"type": "system", "subtype": "init", "session_id": "s1"},
        {"type": "api_error", "error": {"rate_limits": {"resets_at": 999, "rate_limit_type": "seven_day"}}},
        {"type": "result", "subtype": "error_during_execution", "is_error": True},
    ]
    assert run(frames) == LIMIT


def test_rate_limit_category_is_limit():
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "api_retry", "attempt": 1, "error_status": 429, "category": "rate_limit"},
        {"type": "result", "subtype": "error_during_execution", "is_error": True},
    ]
    assert run(frames) == LIMIT


def test_error_max_turns_is_turns():
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "result", "subtype": "error_max_turns", "is_error": True},
    ]
    assert run(frames) == TURNS


def test_error_max_budget_is_budget():
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "result", "subtype": "error_max_budget_usd", "is_error": True},
    ]
    assert run(frames) == BUDGET


def test_bare_error_during_execution_no_limit_signal_is_crash():
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "result", "subtype": "error_during_execution", "is_error": True,
         "errors": ["some internal error"]},
    ]
    assert run(frames) == CRASH


def test_no_result_frame_at_all_is_crash():
    frames = [
        {"type": "system", "subtype": "init", "session_id": "s1"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "working..."}]}},
    ]
    assert run(frames) == CRASH


def test_killed_process_reaped_is_crash():
    frames = [{"type": "system", "subtype": "init"}]
    assert run(frames, reaped=True) == CRASH


def test_inactivity_watchdog_hang_is_distinct_from_crash():
    frames = [{"type": "system", "subtype": "init"}]
    assert run(frames, hung=True) == HANG


def test_refusal_no_fallback_counts_as_refused_even_with_success_subtype():
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "model_refusal_no_fallback", "original_model": "claude-fable-5",
         "api_refusal_category": "bio"},
        {"type": "result", "subtype": "success", "is_error": False},
    ]
    assert run(frames) == REFUSED


def test_refusal_with_fallback_is_still_success():
    # model_refusal_fallback (fallback taken) is NOT a strike per §C — only
    # model_refusal_no_fallback ends the turn refused.
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "model_refusal_fallback", "original_model": "claude-fable-5",
         "fallback_model": "claude-opus-5", "api_refusal_category": "bio"},
        {"type": "result", "subtype": "success", "is_error": False},
    ]
    assert run(frames) == SUCCESS


def test_compact_boundary_is_tracked_not_a_classification_input():
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "compact_boundary", "pre_tokens": 900000, "post_tokens": 50000},
        {"type": "compact_boundary", "pre_tokens": 900000, "post_tokens": 50000},
        {"type": "result", "subtype": "success", "is_error": False},
    ]
    st = RunState()
    for f in frames:
        st.observe(f)
    assert st.compact_boundaries == 2
    assert classify(st) == SUCCESS


def test_supervisor_enforced_turn_ceiling_is_turns():
    # claude 2.1.233 dropped the --max-turns CLI flag the design assumed (2.1.220);
    # fworker counts "assistant" frames itself and SIGTERMs on ceiling.
    st = RunState()
    for _ in range(15):
        st.observe({"type": "assistant", "message": {"content": []}})
    assert st.turns == 15
    st.turns_exceeded = True
    assert classify(st) == TURNS


def test_ambiguous_shape_fails_closed_to_limit():
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "result", "subtype": "some_unforeseen_subtype", "is_error": False},
    ]
    assert run(frames) == LIMIT
