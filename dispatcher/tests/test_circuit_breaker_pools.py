"""circuit_breaker.py per-pool windows (wq#2130, DISPATCH_REBUILD_SPEC §2.1).

A cloud trip is the wq#718 behaviour (DISPATCH_DISARM). A local trip writes
runtime/local_pause.json and never touches DISARM. Attempt records carry `model`; a record
without one counts as cloud. Zero live services: fixtures are hand-written JSON, ntfy is a
fake shim, every path is a scratch tmp file.
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
import local_capacity         # noqa: E402
import notify_sinks           # noqa: E402


@pytest.fixture
def benv(tmp_path, monkeypatch):
    runs_root = tmp_path / "_runs"
    runs_root.mkdir()
    ntfy_log = tmp_path / "ntfy.log"
    fake_ntfy = tmp_path / "fake-ntfy"
    fake_ntfy.write_text(f"#!/usr/bin/env bash\necho \"$@\" >> {ntfy_log}\n")
    fake_ntfy.chmod(fake_ntfy.stat().st_mode | stat.S_IEXEC)
    monkeypatch.delenv("FOREMAN_LOCAL_CAPACITY", raising=False)
    monkeypatch.setattr(cb, "RUNS_ROOT", str(runs_root))
    monkeypatch.setattr(cb, "DISARM_PATH", str(tmp_path / "DISARM"))
    monkeypatch.setattr(local_capacity, "LOCAL_PAUSE_PATH", str(tmp_path / "local_pause.json"))
    monkeypatch.setattr(notify_sinks, "NTFY_BIN", str(fake_ntfy))
    monkeypatch.setattr(notify_sinks, "DESKTOP_SSH_HOST", "")
    monkeypatch.setattr(cb, "WINDOW_MINUTES", 90)
    monkeypatch.setattr(cb, "MIN_SAMPLES", 5)
    monkeypatch.setattr(cb, "FAILURE_RATE_THRESHOLD", 0.75)
    return {"runs": runs_root, "disarm": tmp_path / "DISARM", "ntfy": ntfy_log,
            "pause": tmp_path / "local_pause.json"}


def attempts(benv, base, n, classification, model, age_s=60):
    for i in range(n):
        d = benv["runs"] / f"wq{base + i}"
        d.mkdir(parents=True, exist_ok=True)
        rec = {"ticket": base + i, "attempt": 1, "classification": classification,
               "finished_at": time.time() - age_s}
        if model is not None:
            rec["model"] = model
        (d / "attempt-1.json").write_text(json.dumps(rec))


def test_windows_are_computed_per_pool(benv):
    attempts(benv, 100, 6, "KILLED", "hathor-llama8b")
    attempts(benv, 200, 6, "SUCCESS", "claude-sonnet-5")
    assert cb.failure_rate(pool="local")["rate"] == 1.0
    assert cb.failure_rate(pool="cloud")["rate"] == 0.0
    assert cb.failure_rate()["rate"] == 0.5           # no pool: the old global window


def test_record_without_model_counts_as_cloud(benv):
    attempts(benv, 100, 6, "KILLED", None)
    assert cb.failure_rate(pool="cloud")["total"] == 6
    assert cb.failure_rate(pool="local")["total"] == 0
    assert cb.evaluate()[0] is True                   # cloud trip, as before wq#2130


def test_deepseek_and_group_records_are_local_pool(benv):
    attempts(benv, 100, 3, "KILLED", "deepseek-v4-flash")
    attempts(benv, 200, 3, "KILLED", "local-worker")
    assert cb.failure_rate(pool="local")["total"] == 6


def test_local_trip_writes_pause_not_disarm(benv):
    attempts(benv, 100, 6, "KILLED", "nephthys-qwen14b")
    attempts(benv, 200, 6, "SUCCESS", "claude-sonnet-5")
    now = time.time()
    tripped, stats = cb.check_and_trip_local(now=now)
    assert tripped and stats["rate"] == 1.0
    assert not benv["disarm"].exists()
    pause = json.loads(benv["pause"].read_text())
    assert pause["until"] - now == pytest.approx(60 * 60)
    assert "local pool" in pause["reason"]
    assert benv["ntfy"].exists()
    # ...and the cloud pool is untouched by the same records
    assert cb.check_and_trip(now=now) == (False, cb.failure_rate(now=now, pool="cloud"))
    assert not benv["disarm"].exists()


def test_cloud_trip_is_todays_behaviour_and_writes_no_pause(benv):
    attempts(benv, 100, 6, "KILLED", "claude-sonnet-5")
    tripped, _ = cb.check_and_trip()
    assert tripped and benv["disarm"].exists()
    assert not benv["pause"].exists()
    assert cb.check_and_trip_local()[0] is False


def test_local_failures_do_not_trip_the_cloud_pool(benv):
    attempts(benv, 100, 30, "KILLED", "hathor-llama8b")
    assert cb.check_and_trip()[0] is False
    assert not benv["disarm"].exists()


def test_active_pause_is_not_rewritten_or_renotified(benv):
    attempts(benv, 100, 6, "KILLED", "hathor-llama8b")
    assert cb.check_and_trip_local()[0] is True
    first = benv["pause"].read_text()
    benv["ntfy"].unlink()
    assert cb.check_and_trip_local()[0] is False
    assert benv["pause"].read_text() == first and not benv["ntfy"].exists()


def test_local_dry_run_reports_but_writes_nothing(benv):
    attempts(benv, 100, 6, "KILLED", "hathor-llama8b")
    tripped, _ = cb.check_and_trip_local(dry_run=True)
    assert tripped and not benv["pause"].exists() and not benv["ntfy"].exists()


def test_local_below_min_samples_does_not_trip(benv):
    attempts(benv, 100, 4, "KILLED", "hathor-llama8b")
    assert cb.check_and_trip_local()[0] is False


def test_static_lever_restores_one_global_window(benv, monkeypatch):
    monkeypatch.setenv("FOREMAN_LOCAL_CAPACITY", "static")
    attempts(benv, 100, 6, "KILLED", "hathor-llama8b")
    assert cb.check_and_trip_local() == (False, {})
    assert cb.check_and_trip()[0] is True and benv["disarm"].exists()
    assert not benv["pause"].exists()


def test_pause_expires_and_survives_garbage(benv):
    now = time.time()
    local_capacity.write_pause("x", {}, now=now - 3700)
    assert local_capacity.read_pause(now) is None
    benv["pause"].write_text("not json")
    assert local_capacity.read_pause(now) is None
    local_capacity.write_pause("x", {}, now=now)
    assert local_capacity.read_pause(now + 3599)["reason"] == "x"
