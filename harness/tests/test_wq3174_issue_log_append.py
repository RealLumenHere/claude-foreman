"""wq#3174: issue_log_append.py allocates ISSUE-LOG row numbers under a lock.

Fixture files only, never the live kb log.
"""
import fcntl
import importlib.util
import os
import subprocess
import sys
import time

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import issue_log_append as ila  # noqa: E402

SCRIPT = os.path.join(HARNESS_DIR, "issue_log_append.py")
_probe_spec = importlib.util.spec_from_file_location(
    "probe_pointers",
    os.path.join(os.path.dirname(HARNESS_DIR), "ops",
                 "wq2817-issue-log-pointer-integrity", "probe_pointers.py"),
)
probe = importlib.util.module_from_spec(_probe_spec)
_probe_spec.loader.exec_module(probe)

HEADER = "| # | Issue | Root cause | Solution | Status |\n|---|---|---|---|---|\n"


def _log(tmp_path, body):
    path = tmp_path / "ISSUE-LOG.md"
    path.write_text(HEADER + body)
    return str(path)


def _run(path, *extra):
    return subprocess.run(
        [sys.executable, SCRIPT, "--path", path, *extra],
        capture_output=True, text=True, timeout=30,
    )


def test_next_row_takes_max_not_last_and_ignores_suffix():
    # row 1064 sits mid-file (the live L651 shape); 887b must not count as 888.
    text = HEADER + "| 12 | a | b | c | d |\n| 1064 | x | y | z | w |\n| 887b | e | f | g | h |\n"
    assert ila.next_row(text) == 1065
    assert ila.next_row(HEADER) == 1


def test_append_writes_one_wellformed_row(tmp_path):
    path = _log(tmp_path, "| 7 | a | b | c | SOLVED |")  # no trailing newline
    proc = _run(path, "--issue", "two\nlines | pipe", "--cause", "c",
                "--solution", "s", "--status", "SOLVED")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "8"
    lines = open(path).read().splitlines()
    assert lines[-2] == "| 7 | a | b | c | SOLVED |"
    assert lines[-1] == r"| 8 | two lines \| pipe | c | s | SOLVED |"


def test_missing_field_is_an_error_and_writes_nothing(tmp_path):
    path = _log(tmp_path, "| 7 | a | b | c | SOLVED |\n")
    before = open(path).read()
    proc = _run(path, "--issue", "x", "--cause", "c", "--solution", "s")
    assert proc.returncode == 2 and "--status" in proc.stderr
    assert open(path).read() == before


def test_append_waits_for_the_lock(tmp_path):
    # Falsifiable: without the flock the child appends while we still hold it.
    path = _log(tmp_path, "| 7 | a | b | c | SOLVED |\n")
    with open(path) as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        child = subprocess.Popen(
            [sys.executable, SCRIPT, "--path", path, "--issue", "i", "--cause", "c",
             "--solution", "s", "--status", "OPEN"],
            stdout=subprocess.PIPE, text=True,
        )
        time.sleep(1.0)
        assert child.poll() is None
        assert "| 8 |" not in open(path).read()
        # A writer that appends while we hold the lock is what the child must see.
        with open(path, "a") as fh:
            fh.write("| 8 | raced | c | s | OPEN |\n")
    out, _ = child.communicate(timeout=30)
    assert out.strip() == "9"


def test_parallel_appenders_never_collide(tmp_path):
    path = _log(tmp_path, "| 100 | seed | c | s | SOLVED |\n")
    procs = [
        subprocess.Popen(
            [sys.executable, SCRIPT, "--path", path, "--issue", f"w{i}", "--cause", "c",
             "--solution", "s", "--status", "OPEN"],
            stdout=subprocess.PIPE, text=True,
        )
        for i in range(12)
    ]
    got = sorted(int(p.communicate(timeout=60)[0]) for p in procs)
    assert got == list(range(101, 113))
    found = probe.rows(open(path).read().splitlines())
    assert not [k for k in probe.finding_keys(found) if k.startswith("DUP:")]
