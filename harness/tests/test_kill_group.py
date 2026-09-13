"""wq#1813 ACCEPTANCE (duplicate of the already-fixed wq#1798 gap, pinned directly at
the unit level rather than via the full fworker CLI dispatch that
test_fworker_integration.py:test_watchdog_kill_reaches_grandchild_process already
exercises end-to-end): fworker.py's _kill_proc_tree() must signal the whole process
GROUP (os.killpg), not just the spawned CLI pid, so a Bash-tool grandchild (build,
sleep, dev server) the CLI spawned does not survive the CLI's own death.

Spawns a fake "CLI" child (start_new_session=True, matching run_child's real spawn)
that itself forks a sleeping grandchild and writes the grandchild's pid to a file, then
calls _kill_proc_tree() on the fake CLI child and asserts the grandchild's process
group is gone (pgid lookup raises ProcessLookupError) once the call returns.
"""
import os
import signal
import subprocess
import sys
import time

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402


def _spawn_fake_cli_with_grandchild(pid_file):
    # Mirrors run_child's own spawn (fworker.py ~1240): start_new_session=True puts
    # the child in its own process group, and that child forks a grandchild (a sleep,
    # standing in for a Bash-tool build/server) which inherits the same pgid.
    script = (
        "import os, subprocess, sys, time\n"
        "gc = subprocess.Popen(['sleep', '3600'])\n"
        f"open({pid_file!r}, 'w').write(str(gc.pid))\n"
        "time.sleep(3600)\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", script],
        start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def test_kill_proc_tree_reaches_grandchild(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    proc = _spawn_fake_cli_with_grandchild(str(pid_file))
    try:
        deadline = time.time() + 5
        while not pid_file.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert pid_file.exists(), "fake CLI child never wrote the grandchild pid"
        grandchild_pid = int(pid_file.read_text().strip())

        pgid = os.getpgid(proc.pid)
        assert pgid == os.getpgid(grandchild_pid), (
            "grandchild must share the fake CLI's process group for this test to "
            "prove anything about os.killpg reach")

        fworker._kill_proc_tree(proc, term_timeout=5, kill_timeout=5)

        # A killed process can sit as a zombie briefly before pid reuse; poll for
        # "group gone" instead of a single check.
        deadline = time.time() + 5
        pgid_gone = False
        while time.time() < deadline:
            try:
                os.killpg(pgid, 0)
                time.sleep(0.05)
            except ProcessLookupError:
                pgid_gone = True
                break
        assert pgid_gone, (
            f"process group {pgid} (grandchild pid {grandchild_pid}) still alive "
            f"after _kill_proc_tree — kill path did not reach the group")
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)
