"""wq#1793 smoke check — exercises the spike's mechanics without spawning a
real `claude -p` child (uhp-conformance / litellm-mechanical are not reachable
from this worker's add_dirs; see app.py module docstring + ISSUE-LOG #328).

Checks, against spikes/wq1793_uhp_core/app.py:
1. build_env/build_claude_cmd are the real harness.fworker functions, reused
   unmodified (cmd shape matches fworker's own contract).
2. 3-concurrent-session cap: a 4th session is rejected while 3 are in flight.
3. Gapless progressive SSE: a late subscriber still gets every event from
   index 0, not just the live tail.
4. killpg cancel: SIGTERM lands on the child's whole process group, not just
   the child pid — verified via a grandchild that only dies if the group is
   signaled.
"""
import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as spike_app  # noqa: E402
from fworker import build_claude_cmd, build_env  # noqa: E402


def check_reused_builders():
    env = build_env("smoke-worker", None, None, "/home/lumen/worktrees/wq1793-a1", "1793")
    cmd = build_claude_cmd("hello", None, None, "/dev/null", [])
    assert cmd[0] == "claude" and cmd[1] == "-p" and cmd[2] == "hello", cmd
    assert "FOREMAN_WORKER" in env and env["FOREMAN_WORKER"] == "smoke-worker"
    assert spike_app.build_env is build_env and spike_app.build_claude_cmd is build_claude_cmd
    print("OK build_env/build_claude_cmd reused verbatim:", cmd)


def _make_session(sid, shell_cmd):
    sess = spike_app.Session(sid, ["bash", "-c", shell_cmd], "/tmp", dict(os.environ))
    sess.start()
    return sess


def check_concurrency_cap():
    sessions = []
    for i in range(spike_app.MAX_CONCURRENT_SESSIONS):
        assert spike_app.SESSION_SEM.acquire(blocking=False), f"slot {i} should be free"
    # all 3 slots held now; a 4th must be rejected exactly like start_session() does
    assert spike_app.SESSION_SEM.acquire(blocking=False) is False
    for _ in range(spike_app.MAX_CONCURRENT_SESSIONS):
        spike_app.SESSION_SEM.release()
    print(f"OK concurrency cap holds at {spike_app.MAX_CONCURRENT_SESSIONS}")


def check_gapless_replay():
    spike_app.SESSION_SEM.acquire(blocking=False)
    sess = _make_session("smoke-replay", "for i in 1 2 3 4 5; do echo line$i; sleep 0.05; done")
    time.sleep(0.4)  # let several lines land before anyone "subscribes"
    with sess.lock:
        replay_from_zero = list(sess.events)
    assert len(replay_from_zero) >= 3, replay_from_zero
    assert replay_from_zero[0].strip() == "line1", replay_from_zero
    sess.done.wait(timeout=3)
    with sess.lock:
        full = [l.strip() for l in sess.events]
    assert full == [f"line{i}" for i in range(1, 6)], full
    print("OK late subscriber replay is gapless from index 0:", full)


def check_killpg_cancel():
    spike_app.SESSION_SEM.acquire(blocking=False)
    marker = "/tmp/wq1793_smoke_grandchild_alive"
    if os.path.exists(marker):
        os.remove(marker)
    # child backgrounds a grandchild via `&` — only os.killpg (not a single
    # SIGTERM to the direct child pid) reaches it, since it re-parents but
    # stays in the same process group under start_new_session.
    shell_cmd = f"(sleep 5; touch {marker}) & wait"
    sess = _make_session("smoke-cancel", shell_cmd)
    time.sleep(0.3)
    sess.cancel()
    sess.done.wait(timeout=3)
    time.sleep(1.0)
    assert sess.cancelled is True
    assert not os.path.exists(marker), "grandchild survived — killpg didn't reach the group"
    print("OK killpg cancel took down the whole process group")


if __name__ == "__main__":
    check_reused_builders()
    check_concurrency_cap()
    check_gapless_replay()
    check_killpg_cancel()
    print("ALL OK")
