"""Evidence under _runs is owner-only (wq#2911).

pat-scan graded _runs/wq2707/evidence/attempt-1/replay-AC1.log CRITICAL: the replay log
echoed a worker's fixture tokens and was written 0644 under a 0755 run dir. run_dir must
be 0700 (tightening a pre-existing looser dir too), and every replay log / evidence copy
0600 (tightening a pre-existing looser file too).
"""
import os
import stat
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import acceptance  # noqa: E402
import fworker  # noqa: E402


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_run_dir_is_owner_only_even_under_permissive_umask(tmp_path, monkeypatch):
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path))
    old = os.umask(0o022)
    try:
        assert _mode(fworker.run_dir(9001)) == 0o700
    finally:
        os.umask(old)


def test_run_dir_tightens_a_preexisting_world_readable_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path))
    legacy = tmp_path / "wq9002"
    legacy.mkdir()
    os.chmod(legacy, 0o755)
    assert _mode(fworker.run_dir(9002)) == 0o700


def _fake_runner(argv, **kw):
    return subprocess.CompletedProcess(argv, 0, stdout="gh" "p_" + "Q" * 36 + "\n",
                                       stderr="")


def test_replay_log_and_dir_are_private(tmp_path):
    ev = tmp_path / "evidence" / "attempt-1"
    old = os.umask(0o022)
    try:
        ok, _, _, log = acceptance._replay({"id": "AC1", "cmd": "git status"}, str(tmp_path),
                                           str(ev), _fake_runner)
    finally:
        os.umask(old)
    assert ok and log
    assert _mode(ev) == 0o700
    assert _mode(log) == 0o600


def test_replay_log_rewrite_tightens_a_preexisting_0644_log(tmp_path):
    ev = tmp_path / "ev"
    ev.mkdir()
    stale = ev / "replay-AC1.log"
    stale.write_text("old\n")
    os.chmod(stale, 0o644)
    _, _, _, log = acceptance._replay({"id": "AC1", "cmd": "git status"}, str(tmp_path),
                                      str(ev), _fake_runner)
    assert log == str(stale)
    assert _mode(log) == 0o600
    assert "old" not in stale.read_text()


def test_snapshot_evidence_copy_is_private(tmp_path):
    wt, rd = tmp_path / "wt", tmp_path / "rd"
    wt.mkdir()
    rd.mkdir()
    src = wt / acceptance.EVIDENCE_FILENAME
    src.write_text("{}")
    os.chmod(src, 0o644)
    acceptance.snapshot_evidence(str(wt), str(rd))
    assert _mode(rd / acceptance.EVIDENCE_FILENAME) == 0o600
