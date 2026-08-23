"""dispatch_config_lint.py — lane/config drift probe unit tests (wq#845)."""
import json
import os
import sqlite3
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, DISPATCHER_DIR)
import dispatch_config_lint as dcl  # noqa: E402


def _make_db(path, rows):
    """rows: list of (lane, status) tuples."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, lane TEXT, status TEXT)")
    conn.executemany("INSERT INTO tasks(lane, status) VALUES (?, ?)", rows)
    conn.commit()
    conn.close()


def _make_config(path, lanes):
    with open(path, "w") as f:
        json.dump({"lanes": {lane: {"enabled": True} for lane in lanes}}, f)


def test_fail_when_lane_open_in_db_missing_from_config(tmp_path):
    db = str(tmp_path / "queue.db")
    cfg = str(tmp_path / "config.json")
    _make_db(db, [("build", "open"), ("build", "open"), ("fleet", "open")])
    _make_config(cfg, ["fleet"])

    fails, warns = dcl.lint(config_path=cfg, db_path=db)

    assert len(fails) == 1
    assert "build" in fails[0]
    assert "2 open" in fails[0]
    assert warns == []


def test_no_fail_when_lane_only_has_closed_tickets(tmp_path):
    db = str(tmp_path / "queue.db")
    cfg = str(tmp_path / "config.json")
    _make_db(db, [("build", "done"), ("fleet", "open")])
    _make_config(cfg, ["fleet"])

    fails, warns = dcl.lint(config_path=cfg, db_path=db)

    assert fails == []


def test_warn_when_config_lane_has_zero_tickets(tmp_path):
    db = str(tmp_path / "queue.db")
    cfg = str(tmp_path / "config.json")
    _make_db(db, [("fleet", "open")])
    _make_config(cfg, ["fleet", "lpp"])

    fails, warns = dcl.lint(config_path=cfg, db_path=db)

    assert fails == []
    assert len(warns) == 1
    assert "lpp" in warns[0]


def test_clean_state_no_fails_no_warns(tmp_path):
    db = str(tmp_path / "queue.db")
    cfg = str(tmp_path / "config.json")
    _make_db(db, [("fleet", "open"), ("build", "done")])
    _make_config(cfg, ["fleet", "build"])

    fails, warns = dcl.lint(config_path=cfg, db_path=db)

    assert fails == []
    assert warns == []


def test_check_cmd_dry_run_does_not_write_state(tmp_path, monkeypatch):
    db = str(tmp_path / "queue.db")
    cfg = str(tmp_path / "config.json")
    state = str(tmp_path / "state.json")
    _make_db(db, [("build", "open")])
    _make_config(cfg, [])

    monkeypatch.setattr(dcl, "CONFIG_PATH", cfg)
    monkeypatch.setattr(dcl, "DB_PATH", db)
    monkeypatch.setattr(dcl, "STATE_PATH", state)

    fired, detail = dcl.check_cmd(dry_run=True)

    assert fired is True
    assert "build" in detail
    assert not os.path.exists(state)
