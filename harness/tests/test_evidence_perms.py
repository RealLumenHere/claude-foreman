"""Evidence under _runs is owner-only (wq#2911).

pat-scan graded _runs/wq2707/evidence/attempt-1/replay-AC1.log CRITICAL: the replay log
echoed a worker's fixture tokens and was written 0644 under a 0755 run dir.

run_dir is 0711, not 0700: uid tokeneng re-reads the 0444 grants in it by path, and the
first 0700 cut denied every ticket-end as grant:grant_unreadable
(events/fleet-exec.jsonl, wq2911/wq2985 a1). Traversable-but-unlistable only protects
anything if every other file in it is 0600, so each writer is pinned here under a
permissive umask, including over a pre-existing 0644 file.
"""
import json
import os
import stat
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(HARNESS_DIR), "memory"))
import acceptance  # noqa: E402
import action_gate  # noqa: E402
import episodic  # noqa: E402
import failure_record  # noqa: E402
import fworker  # noqa: E402

import pytest  # noqa: E402


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


@pytest.fixture
def loose_umask():
    old = os.umask(0o022)
    yield
    os.umask(old)


def test_run_dir_is_traversable_not_listable(tmp_path, monkeypatch, loose_umask):
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path))
    assert _mode(fworker.run_dir(9001)) == 0o711


def test_run_dir_tightens_a_preexisting_world_readable_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path))
    legacy = tmp_path / "wq9002"
    legacy.mkdir()
    os.chmod(legacy, 0o755)
    assert _mode(fworker.run_dir(9002)) == 0o711


def test_ticket_end_grant_stays_readable_by_other_uid(tmp_path, monkeypatch, loose_umask):
    """The regression the 0700 cut shipped: tokeneng is 'other' to lumen, so it needs
    o+x on the run dir and o+r on the grant."""
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path))
    path, sha = fworker.write_ticket_end_grant(9003, 1, "fworker-wq9003-a1", 60)
    assert path and sha
    assert _mode(os.path.dirname(path)) & stat.S_IXOTH
    assert _mode(path) == 0o444


def _preexisting_0644(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("[]")
    os.chmod(path, 0o644)


@pytest.mark.parametrize("name,write", [
    ("attempt-1.json", lambda: fworker.write_attempt_state(9004, 1, {"a": 1})),
    ("usage-1.json", lambda: fworker.write_usage_sidecar(9004, 1, {"u": 1})),
    ("mask-hits-1.json",
     lambda: fworker.write_mask_hits(9004, 1, [{"pattern": "x"}])),
    ("episode.jsonl", None),
    ("failure.json", None),
    ("acceptance.json", None),
])
def test_run_dir_writers_are_private(tmp_path, monkeypatch, loose_umask, name, write):
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path))
    d = fworker.run_dir(9004)
    path = os.path.join(d, name)
    _preexisting_0644(path)
    if name == "episode.jsonl":
        episodic.append_event(9004, {"type": "t"}, runs_root=str(tmp_path))
    elif name == "failure.json":
        failure_record.write_failure_record(d, {"v": 1})
    elif name == "acceptance.json":
        acceptance.write_json(path, {"v": 1})
    else:
        write()
    assert _mode(path) == 0o600
    if name != "episode.jsonl":
        with open(path) as f:
            json.load(f)


def test_private_open_append_keeps_content(tmp_path):
    p = tmp_path / "a.log"
    p.write_text("one\n")
    os.chmod(p, 0o644)
    with open(acceptance.private_open(str(p), append=True), "a") as f:
        f.write("two\n")
    assert p.read_text() == "one\ntwo\n"
    assert _mode(p) == 0o600


def test_gate_snapshot_dir_is_owner_only(tmp_path, monkeypatch, loose_umask):
    snaps = tmp_path / "snapshots"
    snaps.mkdir()
    os.chmod(snaps, 0o755)
    monkeypatch.setenv(action_gate.SNAPDIR_ENV, str(snaps))
    assert _mode(action_gate._snapdir()) == 0o700


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
