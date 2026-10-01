"""RELIABILITY_SPEC.md §F fake-claude shim matrix.
Items 1-8 (wq#646, S1 scope): SUCCESS/TURNS/LAUNCH_FAILURE/LIMIT/KILLED/OOM/INDETERMINATE.
Items 9-13 (wq#647, S2 scope): STALL watchdog, SUCCESS-BUT-STUCK, FALSE-COMPLETE, AUTH,
DEGRADED-SUCCESS. Items 14-16 (wq#648, S3 scope): buffered-burst is NOT-A-FAILURE (no
false STALL), partial-line NDJSON reassembly, unknown-frame-type fallthrough.

Each test drives fworker.py end-to-end against a real subprocess invocation of the
fake `claude` shim (tests/fake_claude.py, canned NDJSON + real exit codes) — same
zero-quota pattern as test_fworker_integration.py, whose scratch-repo/scratch-wq/
scratch-env helpers this file reuses.
"""
import json
import os

from test_fworker_integration import (
    make_scratch_repo, make_scratch_env, wq_add, wq_status, wq_show_field, run_fworker,
)


def attempt_record(runs_root, ticket_id, n=1):
    path = os.path.join(runs_root, f"wq{ticket_id}", f"attempt-{n}.json")
    with open(path) as f:
        return json.load(f)


def test_shim_1_clean_success(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(tmp_path, 0, "success")
    ticket_id = wq_add(env, "shim 1: clean success")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "success")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_status(env, ticket_id) == "done"
    assert attempt_record(runs_root, ticket_id)["classification"] == "SUCCESS"


def test_shim_2_real_turn_ceiling_is_turns(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "turn_ceiling")
    ticket_id = wq_add(env, "shim 2: real turn ceiling")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "turn_ceiling")
    # deterministic scenario fails both attempts identically -> blocked after 2, same
    # as any other real TURNS case that never gets a higher-ceiling resume in this test.
    assert wq_status(env, ticket_id) == "blocked"
    record = attempt_record(runs_root, ticket_id)
    assert record["classification"] == "TURNS"
    assert record["turns"] >= 1


def test_shim_3_no_init_instant_exit_is_launch_failure_never_turns(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "no_init_instant_exit")
    ticket_id = wq_add(env, "shim 3: no-init instant exit")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "no_init_instant_exit")
    assert wq_status(env, ticket_id) == "blocked"
    record = attempt_record(runs_root, ticket_id)
    assert record["classification"] == "LAUNCH_FAILURE"
    assert record["classification"] != "TURNS"
    assert record["session_id"] is None


def test_shim_4_turn_setup_failed_is_launch_failure(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "turn_setup_failed")
    ticket_id = wq_add(env, "shim 4: turn_setup_failed")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "turn_setup_failed")
    assert wq_status(env, ticket_id) == "blocked"
    assert attempt_record(runs_root, ticket_id)["classification"] == "LAUNCH_FAILURE"


def test_shim_5_rejected_rate_limit_is_limit_not_crash(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "rejected_limit")
    ticket_id = wq_add(env, "shim 5: 429/rate_limit")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "rejected_limit")
    assert result.returncode == 13, result.stdout + result.stderr  # EXIT_LIMIT
    assert wq_status(env, ticket_id) == "open"
    record = attempt_record(runs_root, ticket_id)
    assert record["classification"] == "LIMIT"
    assert record["classification"] != "CRASH"


def test_shim_6_exit_143_no_result_is_killed(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "sigterm_no_result")
    ticket_id = wq_add(env, "shim 6: exit 143, no result")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "sigterm_no_result")
    assert wq_status(env, ticket_id) == "blocked"
    record = attempt_record(runs_root, ticket_id)
    assert record["classification"] == "KILLED"
    assert record["exit_code"] == 143


def test_shim_7_exit_137_is_oom(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "oom_no_result")
    ticket_id = wq_add(env, "shim 7: exit 137")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "oom_no_result")
    assert wq_status(env, ticket_id) == "blocked"
    record = attempt_record(runs_root, ticket_id)
    assert record["classification"] == "OOM"
    assert record["exit_code"] == 137


def test_shim_8_task_notification_last_no_result_is_indeterminate(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "task_notification_last")
    ticket_id = wq_add(env, "shim 8: task_notification-last drain-death")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "task_notification_last")
    assert wq_status(env, ticket_id) == "blocked"
    record = attempt_record(runs_root, ticket_id)
    assert record["classification"] == "INDETERMINATE"
    assert record["exit_code"] == 0
    assert record["result_subtype"] is None


def test_shim_9_mid_task_silence_triggers_frame_gap_watchdog(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "mid_task_silence")
    env["FAKE_CLAUDE_SILENCE_S"] = "3"
    ticket_id = wq_add(env, "shim 9: mid-task silence > T_gap")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "mid_task_silence",
                          extra_args=["--frame-gap-timeout", "1"])
    assert wq_status(env, ticket_id) == "blocked"
    record = attempt_record(runs_root, ticket_id, n=2)
    assert record["classification"] == "HANG"
    assert record["killed_reason"] == "inactivity"


def test_shim_10_result_then_never_exit_forces_kill_keeps_success(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "success_then_hang")
    ticket_id = wq_add(env, "shim 10: result-then-never-exit")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "success_then_hang",
                          extra_args=["--post-result-grace", "2"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_status(env, ticket_id) == "done"
    record = attempt_record(runs_root, ticket_id)
    assert record["classification"] == "SUCCESS"
    assert record["killed_reason"] == "post_result_stuck"


def test_shim_11_success_with_unmet_criteria_is_false_complete(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "success_false_complete")
    ticket_id = wq_add(env, "shim 11: success but unmet criteria")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "success_false_complete")
    assert wq_status(env, ticket_id) == "blocked"
    record = attempt_record(runs_root, ticket_id, n=2)
    assert record["classification"] == "FALSE_COMPLETE"


def test_shim_11b_legit_block_sentinel_is_terminal_not_retried(tmp_path):
    """wq#749: a worker that calls `wq block` and prints BLOCKED as its last line
    exits 0 with an ordinary result:success frame — classify() alone can't tell that
    apart from shim 11's genuine false-complete. fworker must read the ticket's own
    "blocked" status together with the sentinel and stop, not relabel FALSE_COMPLETE
    and burn a second attempt on an already-blocked ticket."""
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "success_legit_block")
    ticket_id = wq_add(env, "shim 11b: legit block sentinel")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "success_legit_block")
    assert result.returncode == 12, result.stdout + result.stderr  # EXIT_BLOCKED
    assert wq_status(env, ticket_id) == "blocked"
    assert wq_show_field(env, ticket_id, "notes") == "WORK_BLOCKED: needs Frank"
    record = attempt_record(runs_root, ticket_id, n=1)
    # wq#3116 (ea195dce): a confirmed block is recorded as BLOCKED, not the session's
    # SUCCESS, which stays in raw_classification for RCA
    assert record["classification"] == "BLOCKED"
    assert record["raw_classification"] == "SUCCESS"
    # the one-and-only attempt: no second worktree/attempt spawned for a ticket that
    # was already legitimately closed out by the worker itself.
    assert not os.path.exists(os.path.join(runs_root, f"wq{ticket_id}", "attempt-2.json"))


def test_shim_11c_unconfirmed_block_sentinel_still_false_completes(tmp_path):
    """wq#749 adversarial case: a BLOCKED sentinel with no corroborating `wq block`
    call must NOT be trusted on its own — falls through to the ordinary
    FALSE_COMPLETE/retry path, same as shim 11."""
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "success_unconfirmed_block_sentinel")
    ticket_id = wq_add(env, "shim 11c: unconfirmed block sentinel")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "success_unconfirmed_block_sentinel")
    assert wq_status(env, ticket_id) == "blocked"
    record = attempt_record(runs_root, ticket_id, n=2)
    assert record["classification"] == "FALSE_COMPLETE"


def test_shim_11d_claim_error_sentinel_is_terminal_not_retried(tmp_path):
    """wq#749: a worker that hits a claim race and reports CLAIM_ERROR as instructed
    exits 0 with an ordinary result:success frame too — must stop and release, not
    retry under the same stale claim.

    wq#869: the scenario now actually loses the claim (the ticket is reopened out
    from under the attempt) instead of merely saying so, because that DB fact is what
    makes the sentinel terminal."""
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "success_claim_error_sentinel")
    ticket_id = wq_add(env, "shim 11d: claim error sentinel")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "success_claim_error_sentinel")
    assert result.returncode == 10, result.stdout + result.stderr  # EXIT_CLAIM_ERROR
    assert wq_status(env, ticket_id) == "open"  # released, not left claimed
    assert not os.path.exists(os.path.join(runs_root, f"wq{ticket_id}", "attempt-2.json"))


def test_shim_11e_uncorroborated_claim_error_sentinel_still_false_completes(tmp_path):
    """wq#869 adversarial case (the CLAIM_ERROR twin of 11c): a CLAIM_ERROR sentinel
    printed while the ticket is still held by this very attempt is not a claim race —
    nothing raced. It must NOT buy a release + EXIT_CLAIM_ERROR + no-retry on sentinel
    text alone; it falls through to the ordinary FALSE_COMPLETE/retry path, so the
    fabrication stays visible in the attempt record instead of vanishing.

    Before wq#869 the corroboration was `status != "done"`, which the ordinary
    in-progress status "claimed" satisfies, so this scenario exited 10 with no
    attempt-2.json at all."""
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "success_uncorroborated_claim_error_sentinel")
    ticket_id = wq_add(env, "shim 11e: uncorroborated claim error sentinel")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "success_uncorroborated_claim_error_sentinel")
    assert result.returncode != 10, result.stdout + result.stderr  # not EXIT_CLAIM_ERROR
    assert wq_status(env, ticket_id) == "blocked"
    record = attempt_record(runs_root, ticket_id, n=2)
    assert record["classification"] == "FALSE_COMPLETE"


def test_shim_12_401_is_auth(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "auth_401")
    ticket_id = wq_add(env, "shim 12: 401 authentication_failed")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "auth_401")
    assert wq_status(env, ticket_id) == "blocked"
    record = attempt_record(runs_root, ticket_id, n=2)
    assert record["classification"] == "AUTH"


def test_shim_14_buffered_burst_is_not_a_false_stall(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "buffered_burst")
    env["FAKE_CLAUDE_BURST_DELAY_S"] = "1"
    ticket_id = wq_add(env, "shim 14: buffered-pipe burst is NOT-A-FAILURE")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    # gap (1s) stays well under frame-gap-timeout (5s) -> no false STALL/HANG.
    result = run_fworker(env, ticket_id, repo, "buffered_burst",
                          extra_args=["--frame-gap-timeout", "5"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_status(env, ticket_id) == "done"
    record = attempt_record(runs_root, ticket_id)
    assert record["classification"] == "SUCCESS"
    assert record["killed_reason"] is None


def test_shim_15_partial_line_ndjson_reassembly(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "partial_line_split")
    ticket_id = wq_add(env, "shim 15: partial-line NDJSON reassembly")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "partial_line_split")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_status(env, ticket_id) == "done"
    record = attempt_record(runs_root, ticket_id)
    assert record["classification"] == "SUCCESS"
    # 2 real turns emitted (the lead-in frame + the byte-split frame); a parser that
    # mishandled the split would either crash, drop the frame, or double-count it.
    assert record["turns"] == 2


def test_shim_16_unknown_frame_type_falls_through_no_crash(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "unknown_frame_type")
    ticket_id = wq_add(env, "shim 16: unknown frame type, raw fallthrough")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "unknown_frame_type")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_status(env, ticket_id) == "done"
    record = attempt_record(runs_root, ticket_id)
    assert record["classification"] == "SUCCESS"


def test_shim_13_empty_result_text_is_degraded_success(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root, _state_file = make_scratch_env(
        tmp_path, 0, "success_empty_result_text")
    ticket_id = wq_add(env, "shim 13: empty result text")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "success_empty_result_text")
    first = attempt_record(runs_root, ticket_id, n=1)
    assert first["raw_classification"] == "DEGRADED_SUCCESS"
    # no wq done call and no landed commit in this fixture -> same artifact-check
    # fallthrough as FALSE-COMPLETE once do_attempt() has relabeled it.
    assert wq_status(env, ticket_id) == "blocked"
    last = attempt_record(runs_root, ticket_id, n=2)
    assert last["classification"] == "FALSE_COMPLETE"
