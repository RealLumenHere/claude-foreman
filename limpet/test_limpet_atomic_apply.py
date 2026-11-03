"""Unit tests for atomic_apply.run_transaction with an injected runner/clock
(no real subprocess, no real wall clock). (wq#4828.)"""
import subprocess
import unittest.mock as mock

import atomic_apply
from atomic_apply import FileChange, run_transaction


def test_commit_on_healthy_apply_writes_new_content(tmp_path):
    target = tmp_path / "litellm_config.yaml"
    target.write_text("old: config\n")
    res = run_transaction(
        [FileChange(target, b"new: config\n")],
        health_check="probe",
        staging_dir=str(tmp_path / "stage"),
        runner=lambda cmd, t: (0, "ok"),
        now=1.0,
    )
    assert res.committed is True
    assert target.read_bytes() == b"new: config\n"
    assert not (tmp_path / "stage").exists()  # snapshot dropped on commit


def test_on_commit_hook_runs_only_on_success(tmp_path):
    target = tmp_path / "f.yaml"
    target.write_text("old\n")
    seen = []
    run_transaction(
        [FileChange(target, b"new\n")],
        health_check="probe",
        staging_dir=str(tmp_path / "stage"),
        runner=lambda cmd, t: (0, "ok"),
        on_commit=lambda result: seen.append(result),
        now=1.0,
    )
    assert len(seen) == 1 and seen[0].committed is True


def test_failed_health_check_restores_prior_content(tmp_path):
    target = tmp_path / "fleet-exec-policy.yaml"
    target.write_text("old: policy\n")
    res = run_transaction(
        [FileChange(target, b"broken: policy\n")],
        health_check="probe",
        staging_dir=str(tmp_path / "stage"),
        runner=lambda cmd, t: (1, "line1\nservice down"),
        now=2.0,
    )
    assert res.committed is False
    assert target.read_bytes() == b"old: policy\n"
    assert "exit 1" in res.reason and "service down" in res.reason


def test_failed_health_check_deletes_newly_created_file(tmp_path):
    target = tmp_path / "new-unit.service"
    res = run_transaction(
        [FileChange(target, b"[Unit]\n")],
        health_check="probe",
        staging_dir=str(tmp_path / "stage"),
        runner=lambda cmd, t: (1, "boom"),
        now=2.0,
    )
    assert res.committed is False
    assert not target.exists()


def test_failed_health_check_runs_rollback_cmd(tmp_path):
    target = tmp_path / "f.yaml"
    target.write_text("old\n")
    calls = []

    def runner(cmd, t):
        calls.append(cmd)
        return (1, "boom") if cmd == "probe" else (0, "restarted")

    run_transaction(
        [FileChange(target, b"new\n")],
        health_check="probe",
        rollback_cmd="restart-service",
        staging_dir=str(tmp_path / "stage"),
        runner=runner,
        now=2.0,
    )
    assert calls == ["probe", "restart-service"]


def test_health_check_timeout_is_treated_as_failure(tmp_path):
    target = tmp_path / "f.yaml"
    target.write_text("old\n")

    def boom(cmd, t):
        raise subprocess.TimeoutExpired(cmd, t)

    res = run_transaction(
        [FileChange(target, b"new\n")],
        health_check="probe",
        health_timeout=5,
        staging_dir=str(tmp_path / "stage"),
        runner=boom,
        now=2.0,
    )
    assert res.committed is False
    assert "timed out after 5s" in res.reason
    assert target.read_bytes() == b"old\n"


def test_second_file_write_failure_rolls_back_first_file_unharmed(tmp_path):
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text("a-old\n")
    b.write_text("b-old\n")
    orig_write_atomic = atomic_apply._write_atomic
    calls = []

    def flaky_write(target, content):
        calls.append(target)
        if target == b:
            raise PermissionError("no write perms")
        orig_write_atomic(target, content)

    with mock.patch.object(atomic_apply, "_write_atomic", flaky_write):
        res = run_transaction(
            [FileChange(a, b"a-new\n"), FileChange(b, b"b-new\n")],
            health_check="probe",
            staging_dir=str(tmp_path / "stage"),
            runner=lambda cmd, t: (0, "ok"),
            now=3.0,
        )
    assert res.committed is False
    assert "apply failed" in res.reason
    assert a.read_bytes() == b"a-old\n"
    assert b.read_bytes() == b"b-old\n"


def test_runner_raising_uncaught_exception_type_is_rolled_back(tmp_path):
    target = tmp_path / "f.yaml"
    target.write_text("old\n")

    def boom(cmd, t):
        raise ValueError("unexpected runner failure")

    res = run_transaction(
        [FileChange(target, b"new\n")],
        health_check="probe",
        staging_dir=str(tmp_path / "stage"),
        runner=boom,
        now=4.0,
    )
    assert res.committed is False
    assert "apply failed" in res.reason
    assert target.read_bytes() == b"old\n"


def test_rollback_cmd_timeout_does_not_escape_and_is_logged(tmp_path):
    target = tmp_path / "f.yaml"
    target.write_text("old\n")

    def runner(cmd, t):
        if cmd == "probe":
            return (1, "boom")
        raise subprocess.TimeoutExpired(cmd, t)

    res = run_transaction(
        [FileChange(target, b"new\n")],
        health_check="probe",
        rollback_cmd="restart-service",
        staging_dir=str(tmp_path / "stage"),
        runner=runner,
        now=5.0,
    )
    assert res.committed is False
    assert target.read_bytes() == b"old\n"
    assert "rollback_cmd failed" in res.reason


def test_multi_file_transaction_rolls_back_all_files_together(tmp_path):
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text("a-old\n")
    b.write_text("b-old\n")
    res = run_transaction(
        [FileChange(a, b"a-new\n"), FileChange(b, b"b-new\n")],
        health_check="probe",
        staging_dir=str(tmp_path / "stage"),
        runner=lambda cmd, t: (1, "boom"),
        now=2.0,
    )
    assert res.committed is False
    assert a.read_bytes() == b"a-old\n"
    assert b.read_bytes() == b"b-old\n"
