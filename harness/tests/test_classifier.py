"""Classifier unit tests — RELIABILITY_SPEC.md §C/§D verdict engine (wq#646, S1).
Zero quota: canned stream-json fixtures + synthetic exit codes, no claude invocation.
Shim-driven (subprocess) equivalents of these same taxonomy items live in
tests/test_shim_matrix.py, numbered per RELIABILITY_SPEC.md §F.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from classifier import (
    RunState, classify,
    SUCCESS, REFUSED, TURNS, LAUNCH_FAILURE, BUDGET, LIMIT, KILLED, OOM,
    INDETERMINATE, CRASH, HANG, AUTH, DEGRADED_SUCCESS, MODEL_NOT_FOUND,
)


def run(frames, exit_code=None, hung=False, elapsed_s=None, diff_is_empty=None):
    st = RunState()
    for f in frames:
        st.observe(f)
    st.hung = hung
    return classify(st, exit_code, elapsed_s=elapsed_s, diff_is_empty=diff_is_empty)


def test_clean_success():
    frames = [
        {"type": "system", "subtype": "init", "session_id": "s1", "model": "claude-sonnet-5"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}},
        {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01},
    ]
    assert run(frames, exit_code=0) == SUCCESS


def test_success_with_nonzero_exit_is_indeterminate_not_success():
    # §D exit-code cross-check: a success result frame paired with a disagreeing exit
    # code is never trusted as SUCCESS outright.
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "result", "subtype": "success", "is_error": False},
    ]
    assert run(frames, exit_code=1) == INDETERMINATE


def test_thin_no_diff_result_frame_is_launch_failure_wq1499():
    # Pinned to _runs/wq869/attempt-5.json (also wq879/#983/#1122/#1350): a
    # success-shaped result frame with is_error not explicitly False (the real CLI
    # bug — wq869's raw_classification was CRASH, not INDETERMINATE, precisely
    # because is_error was absent), turns=1, exit_code=1, elapsed_s=2.26, and the
    # worker's worktree had no diff at all. Before wq#1499 this fell through the
    # generic catch-all to CRASH — a charged, breaker-tripping attempt for a run
    # that never did any real model work.
    frames = [
        {"type": "system", "subtype": "init", "session_id": "6cf3c809", "model": "opus-5"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "boom"}]}},
        {"type": "result", "subtype": "success", "total_cost_usd": 0},
    ]
    assert run(frames, exit_code=1, elapsed_s=2.264770030975342,
               diff_is_empty=True) == LAUNCH_FAILURE


def test_thin_no_diff_with_is_error_false_is_also_launch_failure():
    # Same shape as test_success_with_nonzero_exit_is_indeterminate_not_success
    # above, but now with the elapsed_s/diff_is_empty evidence supplied: the
    # sharper LAUNCH_FAILURE verdict wins over the coarser INDETERMINATE fallback.
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "result", "subtype": "success", "is_error": False},
    ]
    assert run(frames, exit_code=1, elapsed_s=1.5, diff_is_empty=True) == LAUNCH_FAILURE


def test_thin_result_frame_with_real_diff_stays_crash_not_launch_failure():
    # The no-diff signal is load-bearing: identical timing/turns/exit but the
    # worker actually changed the worktree must NOT be swept into LAUNCH_FAILURE.
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "boom"}]}},
        {"type": "result", "subtype": "success", "total_cost_usd": 0},
    ]
    assert run(frames, exit_code=1, elapsed_s=2.0, diff_is_empty=False) == CRASH


def test_thin_no_diff_fault_never_overrides_limit():
    # A real rate-limit signal is sharper evidence than the thin/no-diff heuristic
    # even if the timing/turns/exit/diff shape happens to match.
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "rate_limit_event", "status": "rejected", "resetsAt": "later"},
        {"type": "result", "subtype": "success", "total_cost_usd": 0},
    ]
    assert run(frames, exit_code=1, elapsed_s=2.0, diff_is_empty=True) == LIMIT


def test_rejected_rate_limit_event_is_limit_not_crash():
    # the ticket's explicit adversarial requirement
    frames = [
        {"type": "system", "subtype": "init"},
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
        {"type": "system", "subtype": "init"},
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


def test_bare_429_status_is_limit():
    # RELIABILITY_SPEC.md §B: limits also manifest as a bare 429 with no category field.
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "api_retry", "error_status": 429},
        {"type": "result", "subtype": "error_during_execution", "is_error": True},
    ]
    assert run(frames) == LIMIT


def test_error_max_turns_with_real_turns_is_turns():
    # §C2/§D: TURNS requires evidence — a result frame AND >=1 real assistant turn.
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}},
        {"type": "result", "subtype": "error_max_turns", "is_error": True},
    ]
    assert run(frames) == TURNS


def test_error_max_turns_with_zero_real_turns_is_not_turns():
    # §D: "no verdict without evidence" — a ceiling result with zero real turns counted
    # is not legal TURNS evidence (e.g. haiku tool-called on the very first raw frame
    # before we'd count a completed turn; PROBE_RESULTS.md's --max-turns 1 case).
    # Falls to the generic result-present bucket, not a guessed TURNS.
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "result", "subtype": "error_max_turns", "is_error": True},
    ]
    assert run(frames) != TURNS


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


def test_terminal_reason_turn_setup_failed_is_launch_failure_never_turns():
    # §C3/§D: turn_setup_failed is a launch failure BEFORE model work — must never be
    # conflated with a genuine turn ceiling, even if a result frame is present.
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "result", "subtype": "error_during_execution", "is_error": True,
         "terminal_reason": "turn_setup_failed"},
    ]
    assert run(frames) == LAUNCH_FAILURE
    assert run(frames) != TURNS


def test_no_init_at_all_is_launch_failure():
    # §C3 shim 3: no init frame observed at all -> LAUNCH_FAILURE, never TURNS/CRASH.
    assert run([]) == LAUNCH_FAILURE


def test_no_result_frame_with_init_and_unknown_exit_is_indeterminate():
    # §C7/§D: absent a result frame, absence-of-evidence must never resolve to a bare
    # CRASH once the spec gives us a sharper answer. With init seen and no signal
    # pinning it to a specific exit code, the conservative default is INDETERMINATE.
    frames = [
        {"type": "system", "subtype": "init", "session_id": "s1"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "working..."}]}},
    ]
    assert run(frames) == INDETERMINATE


def test_no_result_frame_exit_143_is_killed():
    frames = [{"type": "system", "subtype": "init"}]
    assert run(frames, exit_code=143) == KILLED


def test_no_result_frame_exit_137_is_oom():
    frames = [{"type": "system", "subtype": "init"}]
    assert run(frames, exit_code=137) == OOM


def test_task_notification_last_no_result_exit_0_is_indeterminate():
    # the #39632 drain-death signature: task_notification/task_updated as the LAST
    # frame, result frame dropped, process still exits 0.
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}},
        {"type": "task_notification", "task_id": "t1", "status": "done"},
    ]
    assert run(frames, exit_code=0) == INDETERMINATE


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


def test_cli_native_turn_ceiling_is_turns_with_evidence():
    # --max-turns is passed to the CLI (RELIABILITY_SPEC.md §A — NOT dropped in
    # 2.1.233, wq#645/PROBE_RESULTS.md live-confirmed); the CLI itself produces the
    # result:error_max_turns frame this verdict requires as evidence.
    st = RunState()
    for _ in range(15):
        st.observe({"type": "assistant",
                     "message": {"content": [{"type": "tool_use", "name": "Bash"}]}})
    st.observe({"type": "result", "subtype": "error_max_turns", "is_error": True})
    assert st.turns == 15
    assert classify(st) == TURNS


def test_supervisor_backstop_kill_without_result_is_killed_not_turns():
    # §D: "TURNS ... Never legal without a result frame." If the supervisor's own
    # backstop (fworker.TURN_BACKSTOP_BUFFER turns above --max-turns) has to SIGTERM
    # the child because the CLI didn't self-enforce, no result frame arrives — the
    # verdict must be KILLED (exit 143), not a guessed TURNS.
    st = RunState()
    st.observe({"type": "system", "subtype": "init"})
    for _ in range(20):
        st.observe({"type": "assistant",
                     "message": {"content": [{"type": "tool_use", "name": "Bash"}]}})
    st.turns_exceeded = True
    assert classify(st, exit_code=143) == KILLED


def test_thinking_only_assistant_frame_does_not_count_as_a_turn():
    # wq#645: with extended thinking on, the CLI emits the thinking block as its own
    # assistant frame ahead of the real tool_use/text frame, both sharing one
    # stop_reason. fworker was counting both -> real turns were double-counted,
    # tripping its self-enforced ceiling at ~half of max_turns (live evidence:
    # wq#626/wq#603 both hit turns==max_turns after only ~half that many real turns).
    st = RunState()
    for _ in range(20):
        st.observe({"type": "assistant", "message": {"content": [{"type": "thinking",
                                                                    "thinking": "hmm"}]}})
        st.observe({"type": "assistant",
                     "message": {"content": [{"type": "tool_use", "name": "Bash"}]}})
    assert st.turns == 20


def test_mixed_thinking_and_content_block_in_one_frame_counts_once():
    st = RunState()
    st.observe({"type": "assistant", "message": {"content": [
        {"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "done"}]}})
    assert st.turns == 1


def test_empty_content_assistant_frame_does_not_count():
    st = RunState()
    st.observe({"type": "assistant", "message": {"content": []}})
    assert st.turns == 0


def test_api_retry_authentication_failed_is_auth():
    # §C11 AUTH: system/api_retry error:authentication_failed.
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "api_retry", "attempt": 1, "error": "authentication_failed"},
        {"type": "result", "subtype": "error_during_execution", "is_error": True},
    ]
    assert run(frames) == AUTH


def test_api_retry_bare_401_is_auth():
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "api_retry", "attempt": 1, "error_status": 401},
        {"type": "result", "subtype": "error_during_execution", "is_error": True},
    ]
    assert run(frames) == AUTH


def test_auth_never_wins_over_a_genuine_rate_limit():
    # §C4 is checked first — a retry storm carrying both signals reads as LIMIT, the
    # sharper of the two in that shape.
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "api_retry", "attempt": 1, "error_status": 401},
        {"type": "rate_limit_event", "status": "rejected", "rateLimitType": "five_hour",
         "resetsAt": 123, "utilization": 1.0},
        {"type": "result", "subtype": "error_during_execution", "is_error": True},
    ]
    assert run(frames) == LIMIT


def test_success_with_empty_result_text_is_degraded_success():
    # §C12 DEGRADED-SUCCESS (#7124): result:success but `result` is explicitly "".
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "result", "subtype": "success", "is_error": False, "result": ""},
    ]
    assert run(frames, exit_code=0) == DEGRADED_SUCCESS


def test_success_with_missing_result_key_stays_plain_success():
    # the DEGRADED-SUCCESS check must not fire on fixtures/real traffic that simply
    # never populate `result` at all — only an explicit empty string is the #7124 shape.
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "result", "subtype": "success", "is_error": False},
    ]
    assert run(frames, exit_code=0) == SUCCESS


def test_success_with_nonempty_result_text_is_plain_success():
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "result", "subtype": "success", "is_error": False, "result": "done"},
    ]
    assert run(frames, exit_code=0) == SUCCESS


def test_watchdog_kill_after_result_frame_is_not_hang():
    # §C9 SUCCESS-BUT-STUCK: a watchdog kill AFTER a real terminal result already
    # landed must not override that result with HANG — fworker.run_child only sets
    # state.hung on a plain "inactivity" kill (no result ever seen), never on
    # "post_result_stuck", but classify() itself must also not trust state.hung blindly
    # once a result_subtype is on record.
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "result", "subtype": "success", "is_error": False},
    ]
    st = RunState()
    for f in frames:
        st.observe(f)
    st.hung = True
    assert classify(st, exit_code=0) == SUCCESS


def test_ambiguous_result_present_shape_fails_closed_to_crash():
    # S1 keeps the "result frame present, no sharper category" bucket coarse (CRASH);
    # wq#647/wq#648 split AUTH/DEGRADED-SUCCESS/FALSE-COMPLETE out of it.
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "result", "subtype": "some_unforeseen_subtype", "is_error": False},
    ]
    assert run(frames) == CRASH


def test_bad_model_preflight_text_is_model_not_found_not_crash():
    # wq#1442 evidence: wq#1436/wq#1434 ("--model opus-5") died in 1.9s/1 turn/$0 cost.
    # The CLI's own --model preflight surfaces the rejection as a "success"-subtype
    # result frame with `is_error` omitted (not explicitly False), which used to fall
    # into the generic CRASH catch-all and burn a charged attempt on a bad harness
    # dispatch, never the model or ticket doing anything.
    msg = ("There's an issue with the selected model (opus-5). It may not exist or "
           "you may not have access to it. Run --model to pick a different model.")
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": msg}]}},
        {"type": "result", "subtype": "success", "result": msg},
    ]
    assert run(frames, exit_code=1) == MODEL_NOT_FOUND
    assert run(frames, exit_code=1) != CRASH


def test_model_not_found_text_after_real_turns_is_not_model_not_found():
    # Gated to turns <= 1 (the CLI's own rejection message IS the only "turn") — text
    # that merely mentions the phrase after genuine model work already happened must
    # not be misread as the harness-defect preflight shape.
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "turn one"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "turn two"}]}},
        {"type": "result", "subtype": "success",
         "result": "issue with the selected model, mentioned in passing"},
    ]
    assert run(frames, exit_code=1) != MODEL_NOT_FOUND


def test_api_error_category_model_not_found_is_model_not_found():
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "api_error", "error": {"category": "model_not_found"}},
        {"type": "result", "subtype": "some_unforeseen_subtype"},
    ]
    assert run(frames, exit_code=1) == MODEL_NOT_FOUND


def test_bare_404_status_is_model_not_found():
    frames = [
        {"type": "system", "subtype": "init"},
        {"type": "api_retry", "error_status": 404},
        {"type": "result", "subtype": "some_unforeseen_subtype"},
    ]
    assert run(frames, exit_code=1) == MODEL_NOT_FOUND
