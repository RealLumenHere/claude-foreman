"""runaway.py — runaway-ticket detector unit tests (wq#718 AUTO-6).

Zero quota: the real `wq` binary runs against a scratch WQ_DB sqlite file (same fixture
pattern as test_self_heal.py); attempt-N.json fixtures are hand-written stubs (runaway
only reads self_heal.attempt_count()'s file COUNT, never their contents).
"""
import os
import sqlite3
import subprocess
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, DISPATCHER_DIR)
import runaway     # noqa: E402
import self_heal   # noqa: E402

WQ_BIN = "/home/lumen/bin/wq"


@pytest.fixture
def renv(tmp_path, monkeypatch):
    runs_root = tmp_path / "_runs"
    runs_root.mkdir()
    wq_db = tmp_path / "scratch_wq.db"

    monkeypatch.setenv("WQ_DB", str(wq_db))
    monkeypatch.setenv("WQ_AGENT", "test-runaway")
    monkeypatch.setattr(self_heal, "RUNS_ROOT", str(runs_root))
    monkeypatch.setattr(self_heal, "WQ_BIN", WQ_BIN)
    monkeypatch.setattr(runaway, "RUNAWAY_MAX_ATTEMPTS", 5)

    return {"tmp": tmp_path, "runs_root": runs_root, "wq_db": str(wq_db)}


def add_ticket(title="scratch runaway ticket", lane="testlane"):
    p = subprocess.run([WQ_BIN, "add", "--lane", lane, "--title", title],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return int(p.stdout.strip().split()[2])


def set_status(tid, status):
    """Force a ticket's status without going through claim/block (runaway.py only
    cares about the current status column, not how it got there)."""
    db = os.environ["WQ_DB"]
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, tid))
        conn.commit()
    finally:
        conn.close()


def make_attempts(renv, tid, n):
    d = renv["runs_root"] / f"wq{tid}"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        (d / f"attempt-{i}.json").write_text("{}")


# ---- candidate_ticket_ids ----------------------------------------------------------------

def test_candidate_ticket_ids_lists_every_run_dir(renv):
    make_attempts(renv, 1, 2)
    make_attempts(renv, 2, 1)
    (renv["runs_root"] / "not-a-ticket-dir").mkdir()

    assert sorted(runaway.candidate_ticket_ids()) == [1, 2]


def test_candidate_ticket_ids_empty_when_runs_root_missing(renv, monkeypatch):
    monkeypatch.setattr(self_heal, "RUNS_ROOT", str(renv["tmp"] / "does-not-exist"))
    assert runaway.candidate_ticket_ids() == []


# ---- stuck_ticket_ids ----------------------------------------------------------------

def test_below_threshold_not_stuck(renv):
    tid = add_ticket()
    make_attempts(renv, tid, 4)  # < RUNAWAY_MAX_ATTEMPTS=5

    assert runaway.stuck_ticket_ids() == {}


def test_at_threshold_and_not_done_is_stuck(renv):
    tid = add_ticket(lane="testlane")
    make_attempts(renv, tid, 5)

    stuck = runaway.stuck_ticket_ids()
    assert set(stuck) == {tid}
    assert stuck[tid]["attempts"] == 5
    assert stuck[tid]["status"] == "open"
    assert stuck[tid]["lane"] == "testlane"


def test_above_threshold_still_stuck(renv):
    tid = add_ticket()
    make_attempts(renv, tid, 9)

    assert set(runaway.stuck_ticket_ids()) == {tid}


def test_done_ticket_never_stuck_even_at_high_attempt_count(renv):
    tid = add_ticket()
    make_attempts(renv, tid, 8)
    set_status(tid, "done")

    assert runaway.stuck_ticket_ids() == {}


def test_blocked_ticket_at_threshold_is_stuck(renv):
    tid = add_ticket()
    make_attempts(renv, tid, 5)
    set_status(tid, "blocked")

    stuck = runaway.stuck_ticket_ids()
    assert stuck[tid]["status"] == "blocked"


def test_custom_threshold_override(renv):
    tid = add_ticket()
    make_attempts(renv, tid, 2)

    assert runaway.stuck_ticket_ids(threshold=2) == {
        tid: {"attempts": 2, "status": "open", "lane": "testlane"}}
    assert runaway.stuck_ticket_ids(threshold=3) == {}


def test_multiple_tickets_mixed(renv):
    stuck_tid = add_ticket("stuck one")
    make_attempts(renv, stuck_tid, 6)
    fine_tid = add_ticket("fine one")
    make_attempts(renv, fine_tid, 1)
    done_tid = add_ticket("done one")
    make_attempts(renv, done_tid, 7)
    set_status(done_tid, "done")

    assert set(runaway.stuck_ticket_ids()) == {stuck_tid}


# ---- CLI ---------------------------------------------------------------------------------

def test_cmd_list_stuck(renv, capsys):
    tid = add_ticket()
    make_attempts(renv, tid, 5)

    assert runaway.main(["list-stuck"]) == 0
    out = capsys.readouterr().out
    assert f'"{tid}"' in out
