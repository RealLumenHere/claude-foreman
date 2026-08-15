"""RELIABILITY_SPEC.md §F fake-claude shim matrix, items 1-8 (wq#646, S1 scope).

Each test drives fworker.py end-to-end against a real subprocess invocation of the
fake `claude` shim (tests/fake_claude.py, canned NDJSON + real exit codes) — same
zero-quota pattern as test_fworker_integration.py, whose scratch-repo/scratch-wq/
scratch-env helpers this file reuses. Items 9-16 (STALL, SUCCESS-BUT-STUCK,
FALSE-COMPLETE, AUTH, DEGRADED-SUCCESS, buffered-burst/partial-line/unknown-frame
hardening) are wq#647/wq#648 (S2/S3) — not built here.
"""
import json
import os

from test_fworker_integration import (
    make_scratch_repo, make_scratch_env, wq_add, wq_status, run_fworker,
)


def attempt_record(runs_root, ticket_id, n=1):
    path = os.path.join(runs_root, f"wq{ticket_id}", f"attempt-{n}.json")
    with open(path) as f:
        return json.load(f)


def test_shim_1_clean_success(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(tmp_path, 0, "success")
    ticket_id = wq_add(env, "shim 1: clean success")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "success")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_status(env, ticket_id) == "done"
    assert attempt_record(runs_root, ticket_id)["classification"] == "SUCCESS"


def test_shim_2_real_turn_ceiling_is_turns(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(
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
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(
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
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(
        tmp_path, 0, "turn_setup_failed")
    ticket_id = wq_add(env, "shim 4: turn_setup_failed")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "turn_setup_failed")
    assert wq_status(env, ticket_id) == "blocked"
    assert attempt_record(runs_root, ticket_id)["classification"] == "LAUNCH_FAILURE"


def test_shim_5_rejected_rate_limit_is_limit_not_crash(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(
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
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(
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
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(
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
    env, wq_db, events_dir, runs_root, worktrees_root = make_scratch_env(
        tmp_path, 0, "task_notification_last")
    ticket_id = wq_add(env, "shim 8: task_notification-last drain-death")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)

    result = run_fworker(env, ticket_id, repo, "task_notification_last")
    assert wq_status(env, ticket_id) == "blocked"
    record = attempt_record(runs_root, ticket_id)
    assert record["classification"] == "INDETERMINATE"
    assert record["exit_code"] == 0
    assert record["result_subtype"] is None
