"""cfd_actuator.py unit tests (wq#1652).

Zero live services: alerts.db is a tmp sqlite with the fleet alerts schema, a
fake `aq` module (reads AQ_TEST_DB at call time) stands in for
agents/alert-queue/aq.py, and holddown/slock/ntfy/restart/reload are fake
shell scripts that log their invocations — same convention as
ops/wq1649-aq-source-escalate/tests.
"""
import importlib.util
import json
import os
import sqlite3
import stat
import sys
import time

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.dirname(TESTS_DIR)

spec = importlib.util.spec_from_file_location(
    "cfd_actuator", os.path.join(MODULE_DIR, "cfd_actuator.py"))
cfd = importlib.util.module_from_spec(spec)
sys.modules["cfd_actuator"] = cfd
spec.loader.exec_module(cfd)

FAKE_AQ = """
import os, sqlite3, time

def db():
    return sqlite3.connect(os.environ["AQ_TEST_DB"])

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def ingest(source, key, title, message, priority):
    c = db()
    c.execute("INSERT INTO alerts(source,key,title,message,status,count,"
              "priority,first_ts,last_ts) VALUES(?,?,?,?,?,?,?,?,?)",
              (source, key, title, message, "open", 1, priority, now(), now()))
    c.commit(); c.close()
"""


def _script(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    aq_db = tmp_path / "alerts.db"
    conn = sqlite3.connect(str(aq_db))
    conn.executescript("""
        CREATE TABLE alerts(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source TEXT, key TEXT, title TEXT, message TEXT,
          status TEXT, count INTEGER, priority TEXT,
          first_ts TEXT, last_ts TEXT, owner TEXT, note TEXT, resolved_ts TEXT);
    """)
    conn.commit()
    conn.close()
    monkeypatch.setenv("AQ_TEST_DB", str(aq_db))

    aq_dir = tmp_path / "aqdir"
    aq_dir.mkdir()
    (aq_dir / "aq.py").write_text(FAKE_AQ)

    calls = tmp_path / "calls.log"
    fake_true = _script(tmp_path / "fake-true",
                        f"#!/usr/bin/env bash\necho \"$0 $@\" >> {calls}\nexit 0\n")
    fake_false = _script(tmp_path / "fake-false",
                         f"#!/usr/bin/env bash\necho \"$0 $@\" >> {calls}\nexit 1\n")
    ntfy_log = tmp_path / "ntfy.log"
    fake_ntfy = _script(tmp_path / "fake-ntfy",
                        f"#!/usr/bin/env bash\necho \"$@\" >> {ntfy_log}\n")
    restart_log = tmp_path / "restart.log"
    fake_restart = _script(tmp_path / "fake-restart",
                           f"#!/usr/bin/env bash\necho restart >> {restart_log}\n")
    reload_log = tmp_path / "reload.log"
    fake_reload = _script(tmp_path / "fake-reload",
                          f"#!/usr/bin/env bash\necho reload >> {reload_log}\n")

    state_dir = tmp_path / "state"
    monkeypatch.setattr(cfd, "AQ_DIR", str(aq_dir))
    monkeypatch.setattr(cfd, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(cfd, "STATE_FILE", str(state_dir / "state.json"))
    # holddown check: exit 1 = no flag; slock acquire: exit 0 = granted
    monkeypatch.setattr(cfd, "HOLDDOWN_BIN", fake_false)
    monkeypatch.setattr(cfd, "SLOCK_BIN", fake_true)
    monkeypatch.setattr(cfd, "NTFY_BIN", fake_ntfy)
    monkeypatch.setattr(cfd, "RESTART_CMD", [fake_restart])
    monkeypatch.setattr(cfd, "RELOAD_CMD", [fake_reload])
    monkeypatch.setattr(cfd, "ISACTIVE_CMD", ["/bin/true"])
    monkeypatch.setenv("CFD_ACTIVE_POLL_S", "3")

    class Env:
        db_path = str(aq_db)

        def __init__(self):
            self.tmp = tmp_path
            self.ntfy_log = ntfy_log
            self.restart_log = restart_log
            self.reload_log = reload_log
            self.fake_true = fake_true
            self.fake_false = fake_false
            self.monkeypatch = monkeypatch

        def rows(self, **where):
            c = sqlite3.connect(self.db_path)
            c.row_factory = sqlite3.Row
            clause = " AND ".join(f"{k}=?" for k in where) or "1=1"
            out = c.execute(f"SELECT * FROM alerts WHERE {clause}",
                            tuple(where.values())).fetchall()
            c.close()
            return out

        def state(self):
            try:
                with open(cfd.STATE_FILE) as f:
                    return json.load(f)
            except FileNotFoundError:
                return {}

        def set_state(self, **kw):
            os.makedirs(cfd.STATE_DIR, exist_ok=True)
            with open(cfd.STATE_FILE, "w") as f:
                json.dump(kw, f)

    return Env()


def test_request_files_row(env):
    assert cfd.main(["request", "--from", "wadjet-ha", "--kind", "reload",
                     "--reason", "origin flip"]) == 0
    rows = env.rows(source="cfd-restart-req", status="open")
    assert len(rows) == 1 and rows[0]["key"] == "reload:wadjet-ha"


def test_request_escalates_when_drain_dead(env):
    for i in range(cfd.ESCALATE_OPEN_ROWS):
        cfd.main(["request", "--from", f"c{i}", "--reason", "r"])
    assert not env.ntfy_log.exists()
    cfd.main(["request", "--from", "healer", "--reason", "r"])
    assert "NOT DRAINING" in env.ntfy_log.read_text()


def test_drain_noop_without_rows(env):
    assert cfd.main(["drain"]) == 0
    assert not env.restart_log.exists() and not env.reload_log.exists()


def test_drain_restarts_and_resolves(env):
    cfd.main(["request", "--from", "healer", "--kind", "restart",
              "--reason", "port down"])
    cfd.main(["request", "--from", "guardian", "--kind", "restart",
              "--reason", "S2 red"])
    assert cfd.main(["drain"]) == 0
    assert env.restart_log.read_text().count("restart") == 1  # coalesced
    assert not env.rows(source="cfd-restart-req", status="open")
    done = env.rows(source="cfd-restart-req", status="resolved")
    assert len(done) == 2 and "restart done" in done[0]["note"]
    assert env.state()["last_restart_ts"] > 0


def test_cooldown_defers_but_never_drops(env):
    env.set_state(last_restart_ts=time.time())
    cfd.main(["request", "--from", "healer", "--kind", "restart",
              "--reason", "r"])
    assert cfd.main(["drain"]) == 0
    assert not env.restart_log.exists()
    assert len(env.rows(source="cfd-restart-req", status="open")) == 1
    env.set_state(last_restart_ts=time.time() - cfd.COOLDOWN_S - 1)
    assert cfd.main(["drain"]) == 0
    assert env.restart_log.exists()
    assert not env.rows(source="cfd-restart-req", status="open")


def test_reload_exempt_from_cooldown(env):
    env.set_state(last_restart_ts=time.time())
    cfd.main(["request", "--from", "wadjet-ha", "--kind", "reload",
              "--reason", "origin flip"])
    assert cfd.main(["drain"]) == 0
    assert env.reload_log.exists() and not env.restart_log.exists()
    assert not env.rows(source="cfd-restart-req", status="open")


def test_reload_served_while_restart_cools(env):
    env.set_state(last_restart_ts=time.time())
    cfd.main(["request", "--from", "healer", "--kind", "restart", "--reason", "r"])
    cfd.main(["request", "--from", "wadjet-ha", "--kind", "reload", "--reason", "r"])
    assert cfd.main(["drain"]) == 0
    assert env.reload_log.exists() and not env.restart_log.exists()
    open_rows = env.rows(source="cfd-restart-req", status="open")
    assert [r["key"] for r in open_rows] == ["restart:healer"]


def test_restart_supersedes_reload(env):
    cfd.main(["request", "--from", "healer", "--kind", "restart", "--reason", "r"])
    cfd.main(["request", "--from", "wadjet-ha", "--kind", "reload", "--reason", "r"])
    assert cfd.main(["drain"]) == 0
    assert env.restart_log.exists() and not env.reload_log.exists()
    assert not env.rows(source="cfd-restart-req", status="open")


def test_holddown_blocks_action(env):
    env.monkeypatch.setattr(cfd, "HOLDDOWN_BIN", env.fake_true)  # flag ACTIVE
    cfd.main(["request", "--from", "healer", "--kind", "restart", "--reason", "r"])
    assert cfd.main(["drain"]) == 0
    assert not env.restart_log.exists()
    assert len(env.rows(source="cfd-restart-req", status="open")) == 1


def test_slock_deny_defers(env):
    env.monkeypatch.setattr(cfd, "SLOCK_BIN", env.fake_false)
    cfd.main(["request", "--from", "healer", "--kind", "restart", "--reason", "r"])
    assert cfd.main(["drain"]) == 0
    assert not env.restart_log.exists()
    assert len(env.rows(source="cfd-restart-req", status="open")) == 1


def test_restart_failure_pages_after_three(env):
    env.monkeypatch.setattr(cfd, "RESTART_CMD", [env.fake_false])
    cfd.main(["request", "--from", "healer", "--kind", "restart", "--reason", "r"])
    for _ in range(3):
        assert cfd.main(["drain"]) == 1
    assert env.state()["consec_fails"] == 3
    assert "CFD-ACTUATOR FAILING" in env.ntfy_log.read_text()
    assert len(env.rows(source="cfd-restart-req", status="open")) == 1
