"""wq-worker@.service — one worker per node slot (wq#1182).

Zero quota, no real systemd: systemd can't run inside this sandbox, so what's under test
is the actual `flock` invocation shape the unit file uses, exercised directly with the real
`flock` binary against a scratch lockfile — not a Python re-implementation of the guarantee.
The unit file itself is asserted statically to actually use that shape, so an edit that
silently drops the lock cannot pass unnoticed.
"""
import os
import stat
import subprocess

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIT_FILE = os.path.join(HARNESS_DIR, "systemd", "wq-worker@.service")
WRAPPER = os.path.join(HARNESS_DIR, "wq_worker_slot.sh")


def flock_hold(lockfile, hold_seconds):
    """Mirrors the unit file's ExecStart: flock -n LOCK <long-running command>. Returns the
    live Popen so the caller can race a second attempt against it before it exits."""
    return subprocess.Popen(["flock", "-n", str(lockfile), "sleep", str(hold_seconds)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def flock_try(lockfile):
    """Mirrors ExecStartPre: flock -n LOCK -c true. Non-blocking; returns immediately."""
    return subprocess.run(["flock", "-n", str(lockfile), "-c", "true"],
                          capture_output=True, text=True)


def test_second_holder_is_refused_while_the_first_is_running(tmp_path):
    lockfile = tmp_path / "wq-worker-1.lock"
    first = flock_hold(lockfile, hold_seconds=2)
    try:
        # give the first process a moment to actually acquire before the second tries
        for _ in range(50):
            p = subprocess.run(["flock", "-n", str(lockfile), "-c",
                                "test -f " + str(tmp_path / "never")],
                               capture_output=True, text=True)
            if p.returncode == 1:
                break
        second = flock_try(lockfile)
        assert second.returncode == 1, "a second concurrent holder must be refused, not queued"
    finally:
        first.wait(timeout=5)


def test_execstartpre_shape_alone_would_not_catch_a_slow_race(tmp_path):
    """Documents WHY ExecStartPre's own flock (acquire-then-release in one call) cannot be
    the enforcement on its own — proves the failure mode the docstring in the unit file
    warns about, so nobody 'simplifies' this to ExecStartPre-only later."""
    lockfile = tmp_path / "wq-worker-2.lock"
    pre_check = flock_try(lockfile)
    assert pre_check.returncode == 0, "an uncontended pre-check must pass"
    # the lock is released the instant `-c true` returns — a genuinely concurrent
    # ExecStart is not blocked by anything ExecStartPre did
    assert not _lock_is_held(lockfile)


def test_lock_releases_when_the_holder_exits(tmp_path):
    lockfile = tmp_path / "wq-worker-3.lock"
    first = flock_hold(lockfile, hold_seconds=1)
    first.wait(timeout=5)
    second = flock_try(lockfile)
    assert second.returncode == 0, "the lock must be free once its holder has exited"


def _lock_is_held(lockfile):
    p = subprocess.run(["flock", "-n", str(lockfile), "-c", "true"],
                       capture_output=True, text=True)
    return p.returncode != 0


def test_unit_file_execstartpre_and_execstart_both_flock_the_same_per_slot_lockfile():
    text = open(UNIT_FILE).read()
    lines = {ln.split("=", 1)[0]: ln for ln in text.splitlines()
             if ln.startswith(("ExecStartPre=", "ExecStart="))}
    assert "ExecStartPre" in lines and "ExecStart" in lines
    assert "/usr/bin/flock -n" in lines["ExecStartPre"]
    assert "/usr/bin/flock -n" in lines["ExecStart"]
    assert "wq-worker-%i.lock" in lines["ExecStartPre"]
    assert "wq-worker-%i.lock" in lines["ExecStart"]
    assert "%i" in text, "must be a template unit (fixed, per-slot instances)"


def test_unit_file_has_no_install_section_enabling_a_wildcard():
    """'Fixed instance count' is an operator policy this unit cannot self-scale past: no
    [Install]/WantedBy means `systemctl enable wq-worker@N` is always an explicit, individual
    act, never a glob/template-wide enable."""
    text = open(UNIT_FILE).read()
    assert "[Install]" not in text


def test_wrapper_is_executable_and_refuses_without_a_ticket_id():
    st = os.stat(WRAPPER)
    assert st.st_mode & stat.S_IXUSR
    env = dict(os.environ)
    env.pop("TICKET_ID", None)
    env.pop("REPO", None)
    p = subprocess.run(["/bin/bash", WRAPPER, "1"], capture_output=True, text=True, env=env)
    assert p.returncode != 0
    assert "TICKET_ID" in p.stderr
