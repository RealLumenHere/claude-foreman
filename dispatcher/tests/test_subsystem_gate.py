"""wq#2089: the SubsystemHeld gate keeps the dispatcher from spawning a worker that
`wq claim` is certain to refuse (a subsystem already locked by someone else)."""
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gates  # noqa: E402

FMT = "%Y-%m-%d %H:%M:%S"


def stamp(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime(FMT)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "queue.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE slocks(scope TEXT PRIMARY KEY, holder TEXT, acquired_at TEXT, "
              "expires_at TEXT, note TEXT DEFAULT '')")
    c.execute("CREATE TABLE slocks_ro(scope TEXT NOT NULL, holder TEXT NOT NULL, acquired_at TEXT, "
              "expires_at TEXT, note TEXT DEFAULT '', PRIMARY KEY(scope,holder))")
    c.commit()
    monkeypatch.setenv("WQ_DB", str(path))
    yield c
    c.close()


def lock(db, scope, holder, minutes=60, ro=False):
    table = "slocks_ro" if ro else "slocks"
    db.execute(f"INSERT INTO {table}(scope, holder, expires_at) VALUES (?,?,?)",
               (scope, holder, stamp(minutes)))
    db.commit()


def gin(tid, subsystems, ctx=None):
    return SimpleNamespace(tid=tid, t={"subsystems": subsystems},
                           ctx=ctx if ctx is not None else SimpleNamespace())


gate = gates.SubsystemHeld()


def test_no_declared_subsystem_passes_without_reading_the_db(monkeypatch):
    monkeypatch.setenv("WQ_DB", "/nonexistent/queue.db")
    monkeypatch.setattr(gates, "_live_holds", lambda *a, **k: pytest.fail("db read"))
    assert gate.check(gin(1, "")) is gates.PASS


def test_a_live_writer_lock_by_someone_else_blocks(db):
    lock(db, "fleet-ops", "fworker-wq2463")
    res = gate.check(gin(2464, "fleet-ops"))
    assert res.reason == "subsystem_held" and "fleet-ops held by fworker-wq2463" in res.detail
    assert not res.terminal and not res.parkable  # live state, never parked


def test_the_tickets_own_lock_never_blocks_it(db):
    lock(db, "fleet-ops", "fworker-wq2464")
    assert gate.check(gin(2464, "fleet-ops")) is gates.PASS


def test_an_expired_lock_is_free(db):
    lock(db, "fleet-ops", "fworker-wq2463", minutes=-5)
    assert gate.check(gin(2464, "fleet-ops")) is gates.PASS


def test_a_reader_blocks_a_writer_but_not_another_reader(db):
    lock(db, "minerva", "orch-x", ro=True)
    assert gate.check(gin(1, "minerva")).reason == "subsystem_held"
    assert gate.check(gin(2, "minerva:ro")) is gates.PASS


def test_a_live_writer_blocks_a_reader(db):
    lock(db, "minerva", "orch-x")
    assert gate.check(gin(1, "minerva:ro")).reason == "subsystem_held"


def test_siblings_in_one_pass_reserve_the_subsystem(db):
    """The 2026-09-24 incident: five digests on fleet-ops made ready in one pass."""
    ctx = SimpleNamespace()
    assert gate.check(gin(2464, "fleet-ops", ctx)) is gates.PASS
    res = gate.check(gin(2465, "fleet-ops", ctx))
    assert res.reason == "subsystem_held" and "reserved by wq#2464" in res.detail
    assert gate.check(gin(2466, "other-sub", ctx)) is gates.PASS  # a different subsystem is unaffected


def test_two_readers_in_one_pass_do_not_serialize(db):
    ctx = SimpleNamespace()
    assert gate.check(gin(1, "fleet-ops:ro", ctx)) is gates.PASS
    assert gate.check(gin(2, "fleet-ops:ro", ctx)) is gates.PASS
    assert gate.check(gin(3, "fleet-ops", ctx)).reason == "subsystem_held"  # a writer waits for them


def test_a_rejected_ticket_reserves_nothing(db):
    lock(db, "fleet-ops", "fworker-wq2463")
    ctx = SimpleNamespace()
    assert gate.check(gin(2464, "fleet-ops", ctx)).reason == "subsystem_held"
    assert ctx.subs_reserved == {}


def test_unreadable_db_fails_open(monkeypatch, tmp_path):
    monkeypatch.setenv("WQ_DB", str(tmp_path / "missing.db"))
    assert gate.check(gin(1, "fleet-ops")) is gates.PASS


def test_orchestrator_scopes_keep_their_whole_name(db):
    lock(db, "orch:build", "orch-a")
    assert gate.check(gin(1, "orch:build")).reason == "subsystem_held"
    assert gate.check(gin(2, "orch:fleet")) is gates.PASS
