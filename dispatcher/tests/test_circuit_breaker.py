"""circuit_breaker.py — dead-man's-switch unit tests (wq#718 AUTO-6).

Zero quota, zero live services: attempt-N.json fixtures are hand-written JSON, ntfy is
a fake shim, DISARM_PATH is a scratch tmp file. Every trip test asserts the FAIL-SAFE
ordering directly: DISARM_PATH exists even when the ntfy sink is made to fail, proving
the guardrail ("circuit breaker MUST fail-safe even if it can't reach ntfy") rather
than just trusting the code read right.
"""
import json
import os
import stat
import sys
import time

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
REPO = os.path.dirname(DISPATCHER_DIR)
sys.path.insert(0, DISPATCHER_DIR)
sys.path.insert(0, os.path.join(REPO, "harness"))
import circuit_breaker as cb  # noqa: E402
import notify_sinks           # noqa: E402
import fworker                # noqa: E402


def _script(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


@pytest.fixture
def benv(tmp_path, monkeypatch):
    runs_root = tmp_path / "_runs"
    runs_root.mkdir()
    ntfy_log = tmp_path / "ntfy.jsonl"
    fake_ntfy = _script(tmp_path / "fake-ntfy",
                        f"#!/usr/bin/env bash\necho \"$@\" >> {ntfy_log}\n")
    disarm = tmp_path / "DISARM"

    monkeypatch.setattr(cb, "RUNS_ROOT", str(runs_root))
    monkeypatch.setattr(cb, "DISARM_PATH", str(disarm))
    monkeypatch.setattr(notify_sinks, "NTFY_BIN", fake_ntfy)
    monkeypatch.setattr(notify_sinks, "DESKTOP_SSH_HOST", "")
    monkeypatch.setattr(cb, "WINDOW_MINUTES", 90)
    monkeypatch.setattr(cb, "MIN_SAMPLES", 5)
    monkeypatch.setattr(cb, "FAILURE_RATE_THRESHOLD", 0.75)

    return {"tmp": tmp_path, "runs_root": runs_root, "ntfy_log": ntfy_log, "disarm": disarm}


def write_attempt(runs_root, tid, n, classification, finished_at):
    d = runs_root / f"wq{tid}"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"attempt-{n}.json").write_text(json.dumps(
        {"ticket": tid, "attempt": n, "classification": classification,
         "finished_at": finished_at}))


def ntfy_lines(benv):
    if not benv["ntfy_log"].exists():
        return []
    return benv["ntfy_log"].read_text().splitlines()


# ---- fworker taxonomy cross-check ------------------------------------------------------

def test_catastrophic_classes_match_fworker_infra_crash_bucket():
    """CATASTROPHIC_CLASSES is a deliberate copy of fworker's INFRA_CRASH bucket (not an
    import — see circuit_breaker.py's module docstring) — this test is the tripwire
    that catches the two ever drifting apart."""
    assert cb.CATASTROPHIC_CLASSES == fworker._INFRA_CRASH_CLASSIFICATIONS


# ---- failure_rate / general_failure_rate -----------------------------------------------

def test_failure_rate_counts_catastrophic_classes_only(benv):
    now = time.time()
    write_attempt(benv["runs_root"], 1, 1, "SUCCESS", now - 60)
    write_attempt(benv["runs_root"], 2, 1, "KILLED", now - 120)
    write_attempt(benv["runs_root"], 3, 1, "TURNS", now - 180)  # LIMIT_HIT, not counted
    write_attempt(benv["runs_root"], 4, 1, "REFUSED", now - 240)  # WORK_BLOCKED, not counted

    stats = cb.failure_rate(now=now)
    assert stats == {"total": 4, "failures": 1, "rate": 0.25, "window_minutes": 90}


def test_general_failure_rate_counts_any_non_success(benv):
    now = time.time()
    write_attempt(benv["runs_root"], 1, 1, "SUCCESS", now - 60)
    write_attempt(benv["runs_root"], 2, 1, "DEGRADED_SUCCESS", now - 60)
    write_attempt(benv["runs_root"], 3, 1, "TURNS", now - 60)
    write_attempt(benv["runs_root"], 4, 1, "KILLED", now - 60)

    stats = cb.general_failure_rate(90, now=now)
    assert stats == {"total": 4, "failures": 2, "rate": 0.5, "window_minutes": 90}


def test_window_excludes_stale_attempts(benv):
    now = time.time()
    write_attempt(benv["runs_root"], 1, 1, "KILLED", now - 200 * 60)  # outside 90m window
    write_attempt(benv["runs_root"], 2, 1, "KILLED", now - 10 * 60)

    stats = cb.failure_rate(now=now)
    assert stats["total"] == 1 and stats["failures"] == 1


def test_corrupt_attempt_file_skipped_not_fatal(benv):
    now = time.time()
    write_attempt(benv["runs_root"], 1, 1, "KILLED", now - 60)
    d = benv["runs_root"] / "wq2"
    d.mkdir()
    (d / "attempt-1.json").write_text("{not json")

    stats = cb.failure_rate(now=now)
    assert stats["total"] == 1


# ---- evaluate() / trip threshold --------------------------------------------------------

def test_not_tripped_below_min_samples(benv):
    now = time.time()
    for i in range(4):  # 4 < MIN_SAMPLES=5, all catastrophic
        write_attempt(benv["runs_root"], i, 1, "KILLED", now - 60)
    tripped, stats = cb.evaluate(now=now)
    assert not tripped and stats["total"] == 4


def test_not_tripped_below_threshold(benv):
    now = time.time()
    for i in range(3):
        write_attempt(benv["runs_root"], i, 1, "KILLED", now - 60)
    for i in range(5):
        write_attempt(benv["runs_root"], 10 + i, 1, "SUCCESS", now - 60)
    tripped, stats = cb.evaluate(now=now)  # 3/8 = 37.5% < 75%
    assert not tripped


def test_tripped_above_threshold_and_min_samples(benv):
    now = time.time()
    for i in range(7):
        write_attempt(benv["runs_root"], i, 1, "KILLED", now - 60)
    for i in range(1):
        write_attempt(benv["runs_root"], 10 + i, 1, "SUCCESS", now - 60)
    tripped, stats = cb.evaluate(now=now)  # 7/8 = 87.5% > 75%, total=8 >= 5
    assert tripped and stats["rate"] == pytest.approx(0.875)


# ---- disarm() fail-safe ordering ---------------------------------------------------------

def test_check_and_trip_disarms_and_notifies(benv):
    now = time.time()
    for i in range(7):
        write_attempt(benv["runs_root"], i, 1, "KILLED", now - 60)

    tripped, stats = cb.check_and_trip(now=now)

    assert tripped
    assert benv["disarm"].exists()
    lines = ntfy_lines(benv)
    assert len(lines) == 1 and "-p urgent" in lines[0] and "TRIPPED" in lines[0]


def test_check_and_trip_dry_run_no_side_effects(benv):
    now = time.time()
    for i in range(7):
        write_attempt(benv["runs_root"], i, 1, "KILLED", now - 60)

    tripped, stats = cb.check_and_trip(now=now, dry_run=True)

    assert tripped  # still reports the truth
    assert not benv["disarm"].exists()  # but takes zero action
    assert ntfy_lines(benv) == []


def test_disarm_fail_safe_even_when_ntfy_unreachable(benv, monkeypatch):
    """The guardrail's exact wording: disarm must land even if ntfy can't be reached."""
    broken = benv["tmp"] / "broken-ntfy"
    broken.write_text("#!/usr/bin/env bash\nexit 1\n")
    broken.chmod(broken.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(notify_sinks, "NTFY_BIN", str(broken))

    result = cb.disarm("test reason")

    assert benv["disarm"].exists()
    assert result["ntfy"] is False


def test_disarm_fail_safe_even_when_notify_sinks_raises(benv, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("simulated notify_sinks crash")
    monkeypatch.setattr(notify_sinks, "notify_critical", boom)

    cb.disarm("test reason")

    assert benv["disarm"].exists()


def test_is_armed_reflects_disarm_path(benv):
    assert cb.is_armed() is True
    benv["disarm"].write_text("")
    assert cb.is_armed() is False


# ---- CLI ---------------------------------------------------------------------------------

def test_cmd_status_reports_without_side_effects(benv, capsys):
    assert cb.main(["status"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["armed"] is True and out["tripped_now"] is False
    assert not benv["disarm"].exists()


def test_cmd_test_ntfy_never_touches_disarm(benv, capsys):
    assert cb.main(["test-ntfy"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ntfy"] is True
    assert not benv["disarm"].exists()
    lines = ntfy_lines(benv)
    assert len(lines) == 1 and "TEST" in lines[0]
