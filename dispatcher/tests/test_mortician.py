"""mortician.py — terminal-failure triage tests (wq#2486).

Acceptance (ticket note, wq#3033 re-gate): a seeded terminally-failed ticket is classified
exactly once, produces one durable record naming the root cause, is marked so redispatch
will not retry it, and is NOT re-processed on a second run of the pass.

Real /home/lumen/bin/wq runs `wq note` against a scratch WQ_DB (conftest keeps the live
queue.db out of reach); the dream sink is a tmp dir via mortician.DREAM_DIR."""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mortician  # noqa: E402
import self_heal  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.path.exists("/home/lumen/bin/wq"), reason="wq binary not present")

OLD = "2020-01-01 00:00:00"  # far past MORT_GRACE_HOURS

TASKS_DDL = """CREATE TABLE tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, lane TEXT DEFAULT 'auto', pri INTEGER DEFAULT 2,
  title TEXT, detail TEXT, deps TEXT DEFAULT '', status TEXT DEFAULT 'open',
  claimed_by TEXT, claimed_at TEXT, done_at TEXT, notes TEXT DEFAULT '', created_at TEXT,
  block_reason TEXT, blocked_at TEXT, review_reason TEXT, reopened_count INTEGER DEFAULT 0)"""


def seed(db, **kw):
    cols = dict(lane="auto", pri=2, title="t", detail="", status="blocked",
                notes="", created_at=OLD, block_reason=None, blocked_at=OLD,
                review_reason=None, reopened_count=0)
    cols.update(kw)
    c = sqlite3.connect(db)
    cur = c.execute(
        "INSERT INTO tasks(lane,pri,title,detail,status,notes,created_at,block_reason,"
        "blocked_at,review_reason,reopened_count) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (cols["lane"], cols["pri"], cols["title"], cols["detail"], cols["status"],
         cols["notes"], cols["created_at"], cols["block_reason"], cols["blocked_at"],
         cols["review_reason"], cols["reopened_count"]))
    c.commit()
    tid = cur.lastrowid
    c.close()
    return tid


def notes_of(db, tid):
    c = sqlite3.connect(db)
    r = c.execute("SELECT notes FROM tasks WHERE id=?", (tid,)).fetchone()
    c.close()
    return r[0] or ""


def morgue_rows(db):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT * FROM morgue").fetchall()
    c.close()
    return rows


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = str(tmp_path / "wq.db")
    c = sqlite3.connect(db)
    c.execute(TASKS_DDL)
    c.commit()
    c.close()
    monkeypatch.setattr(mortician, "WQ_DB", db)
    monkeypatch.setattr(mortician, "DREAM_DIR", str(tmp_path / "dream"))
    # keep attempt_count away from the real _runs tree: a scratch tid like 1 would
    # otherwise read /home/lumen/foreman/_runs/wq1
    monkeypatch.setattr(self_heal, "RUNS_ROOT", str(tmp_path / "runs"))
    return db


def test_seeded_dead_ticket_triaged_exactly_once(env, capsys):
    tid = seed(env, block_reason="WORK_BLOCKED: target repo was deleted upstream")
    triaged, _, errors = mortician.run_pass()
    assert (triaged, errors) == (1, 0)

    rows = morgue_rows(env)
    assert len(rows) == 1 and rows[0]["cause"] == "WORK_BLOCKED"
    assert rows[0]["marked_at"]
    record = rows[0]["record_path"]
    assert os.path.isfile(record)
    body = open(record).read()
    assert "WORK_BLOCKED" in body and "PROPOSAL-ONLY" in body
    marked = notes_of(env, tid)
    assert marked.count("[mortician]") == 1
    assert "do-not-redispatch" in marked

    # second run: NOT re-processed — no second record, no second note, no reclassify
    triaged2, _, errors2 = mortician.run_pass()
    assert (triaged2, errors2) == (0, 0)
    assert len(morgue_rows(env)) == 1
    assert notes_of(env, tid).count("[mortician]") == 1


def test_grace_window_holds_fresh_blocks(env):
    import datetime
    fresh = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    seed(env, block_reason="WORK_BLOCKED: maybe transient", blocked_at=fresh)
    triaged, skipped, _ = mortician.run_pass()
    assert triaged == 0 and skipped == 1
    assert morgue_rows(env) == []


def test_healable_rung_fault_left_to_janitor(env):
    seed(env, block_reason="RUNG_FAULT: backend 503")
    triaged, skipped, _ = mortician.run_pass()
    assert triaged == 0 and skipped == 1
    assert morgue_rows(env) == []


def test_rejected_row_is_terminal(env):
    tid = seed(env, status="rejected", blocked_at=None,
               review_reason="critic: claimed done, tests never ran")
    triaged, _, _ = mortician.run_pass()
    assert triaged == 1
    rows = morgue_rows(env)
    assert rows[0]["cause"] == "REVIEW_REJECTED" and rows[0]["ticket_id"] == tid


def test_transient_record_failure_retries_without_double_fire(env, monkeypatch):
    tid = seed(env, block_reason="HARNESS_DEFECT: allowlist refused the only viable command")

    orig = mortician.write_record

    def boom(row, cause, detail):
        raise OSError("dream dir unwritable")
    monkeypatch.setattr(mortician, "write_record", boom)
    triaged, _, errors = mortician.run_pass()
    assert triaged == 0 and errors == 1
    rows = morgue_rows(env)  # step 1 landed, steps 2/3 pending — classification frozen
    assert len(rows) == 1 and rows[0]["recorded_at"] is None and rows[0]["marked_at"] is None
    assert "[mortician]" not in notes_of(env, tid)

    monkeypatch.setattr(mortician, "write_record", orig)
    triaged, _, errors = mortician.run_pass()
    assert triaged == 1 and errors == 0
    assert notes_of(env, tid).count("[mortician]") == 1
    assert len(morgue_rows(env)) == 1


def test_non_terminal_statuses_are_never_touched(env):
    """wq#2630 outside-population probe: frank_gated is pending a decision and done is
    success — a sweep that grabs either would be the storm the ticket forbids."""
    a = seed(env, status="frank_gated", blocked_at=None)
    b = seed(env, status="done", blocked_at=None)
    c = seed(env, status="open", blocked_at=None)
    triaged, _, errors = mortician.run_pass()
    assert (triaged, errors) == (0, 0)
    assert morgue_rows(env) == []
    for tid in (a, b, c):
        assert "[mortician]" not in notes_of(env, tid)


def test_is_triaged_gates_janitor_release(env):
    tid = seed(env, block_reason="WORK_BLOCKED: impossible precondition")
    assert mortician.is_triaged(tid) is False  # untriaged -> janitor free to act
    mortician.run_pass()
    assert mortician.is_triaged(tid) is True   # triaged -> janitor must skip
    assert mortician.is_triaged(tid + 999) is False
