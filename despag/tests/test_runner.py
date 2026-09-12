"""wq#1806 ACCEPTANCE: despag/runner/turn.py + canonical.py, proven against a fake
backend script (fake_backend.py) that prints real Claude Code stream-json frames at a
controlled pace and can spawn its own sleeping grandchild. No real `claude -p` is ever
spawned by this file.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from despag.runner.turn import Turn, TurnRegistry

FAKE_BACKEND = str(Path(__file__).resolve().parent / "fake_backend.py")


def _cmd(mode: str, **kwargs: object) -> list[str]:
    cmd = [sys.executable, FAKE_BACKEND, "--mode", mode]
    for k, v in kwargs.items():
        cmd += [f"--{k.replace('_', '-')}", str(v)]
    return cmd


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _run_async(turn: Turn) -> threading.Thread:
    th = threading.Thread(target=turn.run, daemon=True)
    th.start()
    return th


def test_killpg_kills_the_grandchild(tmp_path):
    turn = Turn("t-grandchild", _cmd("grandchild", interval=0.2, count=1000), str(tmp_path),
                dict(os.environ), timeout_seconds=60, frame_gap_s=30)
    th = _run_async(turn)

    grandchild_pid = None
    deadline = time.time() + 10
    while time.time() < deadline and grandchild_pid is None:
        for frame in turn.snapshot_frames():
            if frame.get("type") == "test_grandchild_pid":
                grandchild_pid = frame["pid"]
                break
        time.sleep(0.05)
    assert grandchild_pid is not None, "fake backend never reported its grandchild pid"
    assert _pid_alive(grandchild_pid), "grandchild died before the test could cancel the turn"

    turn.cancel()
    th.join(timeout=10)
    assert turn.done.is_set()
    assert turn.status == "cancelled"

    deadline = time.time() + 5
    while time.time() < deadline and _pid_alive(grandchild_pid):
        time.sleep(0.05)
    assert not _pid_alive(grandchild_pid), (
        "grandchild survived cancel — killpg must reach the whole process group, "
        "not just the CLI pid (PHASE0_WIRING_SPEC.md E.1)")


def test_wall_clock_cap_times_out(tmp_path):
    turn = Turn("t-timeout", _cmd("steady", interval=0.05, count=10000), str(tmp_path),
                dict(os.environ), timeout_seconds=0.5, frame_gap_s=30)
    th = _run_async(turn)
    th.join(timeout=15)
    assert turn.done.is_set()
    assert turn.killed_reason == "wall_clock"
    assert turn.status == "timeout"


def test_frame_gap_watchdog_hangs(tmp_path):
    turn = Turn("t-hang", _cmd("silent", sleep_seconds=30), str(tmp_path),
                dict(os.environ), timeout_seconds=60, frame_gap_s=0.5)
    th = _run_async(turn)
    th.join(timeout=15)
    assert turn.done.is_set()
    assert turn.killed_reason == "inactivity"
    assert turn.status == "hang"


def test_api_error_scrub_rewrites_result(tmp_path):
    turn = Turn("t-apierr", _cmd("api_error"), str(tmp_path),
                dict(os.environ), timeout_seconds=30, frame_gap_s=30)
    th = _run_async(turn)
    th.join(timeout=15)
    assert turn.done.is_set()

    frames = turn.snapshot_frames()
    assert [f for f in frames if f.get("type") == "assistant"] == [], (
        "the API-error-only assistant message must be dropped entirely, not passed through")
    result_frames = [f for f in frames if f.get("type") == "result"]
    assert len(result_frames) == 1
    rf = result_frames[0]
    assert rf["subtype"] == "error"
    assert rf["is_error"] is True
    assert "API Error" in rf["result"]
    assert turn.status == "failed"


def test_fd_count_stable_across_50_turns(tmp_path):
    def run_one(i: int) -> Turn:
        turn = Turn(f"t-fd-{i}", _cmd("quick"), str(tmp_path), dict(os.environ),
                    timeout_seconds=30, frame_gap_s=30)
        turn.run()   # "quick" exits almost immediately — synchronous is fine here
        return turn

    run_one(-1)   # let any one-time lazy fd allocation (import caches, etc.) settle
    before = len(os.listdir("/proc/self/fd"))
    for i in range(50):
        turn = run_one(i)
        assert turn.status == "completed"
    after = len(os.listdir("/proc/self/fd"))
    assert after - before <= 3, (
        f"fd count grew from {before} to {after} across 50 turns — "
        "_release_proc is not closing every pipe (PHASE0_WIRING_SPEC.md E.7)")


def test_cancel_before_spawn_race_is_handled(tmp_path):
    turn = Turn("t-race", _cmd("silent", sleep_seconds=30), str(tmp_path),
                dict(os.environ), timeout_seconds=60, frame_gap_s=30)
    turn.cancel()   # cancel before run() has even spawned the child
    th = _run_async(turn)
    th.join(timeout=10)
    assert turn.done.is_set()
    assert turn.status == "cancelled"


def test_evict_finished_drops_old_done_records(tmp_path):
    registry = TurnRegistry()
    turn = registry.start("t-evict", _cmd("quick"), str(tmp_path), dict(os.environ),
                           timeout_seconds=30, frame_gap_s=30)
    assert turn.done.wait(timeout=10)
    assert registry.get("t-evict") is turn

    far_future = turn.started_at + 21600 + 30 * 60 + 1
    gone = registry.evict_finished(now=far_future)
    assert gone == 1
    assert registry.get("t-evict") is None
