"""disk_pct_alarm.py unit tests (wq#3171).

Zero live filesystems: alerts.db is a tmp sqlite with the fleet alerts schema,
a fake `aq` module (same convention as
ops/wq2851-aer-storm-alarm/tests/test_aer_storm_alarm.py) stands in for
agents/alert-queue/aq.py, ntfy-send is a logging fake, and `check()`'s disk
probe is replaced with a synthetic `prober(name, path) -> (pct, error)`
function so /data/lumen's real usage never has to be touched (and can't be —
/data/lumen is outside every --add-dir root this worker was granted).
"""
import importlib.util
import os
import sqlite3
import stat
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.dirname(TESTS_DIR)

spec = importlib.util.spec_from_file_location(
    "disk_pct_alarm", os.path.join(MODULE_DIR, "disk_pct_alarm.py"))
dpa = importlib.util.module_from_spec(spec)
sys.modules["disk_pct_alarm"] = dpa
spec.loader.exec_module(dpa)

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
    sys.modules.pop("aq", None)

    ntfy_log = tmp_path / "ntfy.log"
    fake_ntfy = _script(tmp_path / "fake-ntfy", f"#!/usr/bin/env bash\necho \"$*\" >> {ntfy_log}\n")

    state_dir = tmp_path / "state"
    monkeypatch.setattr(dpa, "AQ_DIR", str(aq_dir))
    monkeypatch.setattr(dpa, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(dpa, "NTFY_BIN", fake_ntfy)

    class Env:
        db_path = str(aq_db)

        def rows(self, **where):
            c = sqlite3.connect(self.db_path)
            c.row_factory = sqlite3.Row
            clause = " AND ".join(f"{k}=?" for k in where) or "1=1"
            out = c.execute(f"SELECT * FROM alerts WHERE {clause}", tuple(where.values())).fetchall()
            c.close()
            return out

        def ntfy_lines(self):
            return ntfy_log.read_text().splitlines() if ntfy_log.exists() else []

    yield Env()
    sys.modules.pop("aq", None)


RULE = {"raise_at_or_above": 90, "clear_below": 85}
CFG = {"defaults": dict(RULE), "mounts": {"data-lumen": {"path": "/data/lumen"}}}


def const_prober(pct, error=None):
    def _p(name, path):
        return pct, error
    return _p


# ------------------------------------------------------------- next_state ---
def test_next_state_raises_at_or_above_threshold():
    assert dpa.next_state(False, 90, RULE) is True
    assert dpa.next_state(False, 89.9, RULE) is False


def test_next_state_hysteresis_does_not_clear_mid_band():
    # firing, pct drops to 87: below raise but still >= clear_below -> stays firing
    assert dpa.next_state(True, 87, RULE) is True
    assert dpa.next_state(True, 84.9, RULE) is False


def test_next_state_probe_failure_carries_prior_state():
    assert dpa.next_state(True, None, RULE) is True
    assert dpa.next_state(False, None, RULE) is False


# ------------------------------------------------------------------ check ---
def test_check_raises_one_aq_row_and_one_ntfy(env):
    verdicts = dpa.check(CFG, prober=const_prober(97))
    assert verdicts[0]["firing"] is True
    rows = env.rows(source="disk-pct", key="disk-pct:data-lumen", status="open")
    assert len(rows) == 1
    assert any("97" in ln for ln in env.ntfy_lines())


def test_check_sustained_firing_does_not_re_raise_or_spam(env):
    dpa.check(CFG, prober=const_prober(97))
    dpa.check(CFG, prober=const_prober(98))
    rows = env.rows(source="disk-pct", key="disk-pct:data-lumen", status="open")
    assert len(rows) == 1
    ntfy_calls = [ln for ln in env.ntfy_lines() if "Disk" in ln and "RECOVERED" not in ln]
    assert len(ntfy_calls) == 1


def test_check_below_threshold_never_fires(env):
    for _ in range(3):
        verdicts = dpa.check(CFG, prober=const_prober(50))
    assert verdicts[0]["firing"] is False
    assert env.rows(source="disk-pct") == []


def test_check_clears_and_resolves_row_on_recovery(env):
    dpa.check(CFG, prober=const_prober(97))
    assert len(env.rows(source="disk-pct", status="open")) == 1
    verdicts = dpa.check(CFG, prober=const_prober(50))
    assert verdicts[0]["firing"] is False
    assert env.rows(source="disk-pct", status="open") == []
    resolved = env.rows(source="disk-pct", status="resolved")
    assert len(resolved) == 1
    assert any("RECOVERED" in ln for ln in env.ntfy_lines())


def test_check_dry_run_never_acts(env):
    verdicts = dpa.check(CFG, dry_run=True, prober=const_prober(99))
    assert verdicts[0]["firing"] is True
    assert env.rows(source="disk-pct") == []
    assert env.ntfy_lines() == []


def test_check_probe_error_does_not_flap_a_firing_mount(env):
    dpa.check(CFG, prober=const_prober(97))
    dpa.check(CFG, prober=const_prober(None, error="OSError: no such mount"))
    rows = env.rows(source="disk-pct", status="open")
    assert len(rows) == 1  # still firing, no spurious clear


# ----------------------------------------------------------------- config ---
def test_validate_rejects_inverted_hysteresis():
    bad = {"defaults": {"raise_at_or_above": 80, "clear_below": 90},
           "mounts": {"data-lumen": {"path": "/data/lumen"}}}
    with pytest.raises(dpa.ConfigError):
        dpa.validate(bad)


def test_validate_rejects_empty_mounts():
    bad = {"defaults": dict(RULE), "mounts": {}}
    with pytest.raises(dpa.ConfigError):
        dpa.validate(bad)


def test_validate_rejects_mount_missing_path():
    bad = {"defaults": dict(RULE), "mounts": {"data-lumen": {}}}
    with pytest.raises(dpa.ConfigError):
        dpa.validate(bad)


def test_default_config_covers_data_lumen_at_90_percent():
    cfg = dpa.default_config()
    assert cfg["mounts"]["data-lumen"]["path"] == "/data/lumen"
    assert cfg["defaults"]["raise_at_or_above"] == 90


def test_mount_can_override_default_threshold():
    cfg = {"defaults": dict(RULE),
           "mounts": {"scratch": {"path": "/scratch", "raise_at_or_above": 95, "clear_below": 92}}}
    dpa.validate(cfg)
    rule = dpa.mount_rule(cfg, "scratch", cfg["mounts"]["scratch"])
    assert rule == {"raise_at_or_above": 95, "clear_below": 92}


# ------------------------------------------------------------------- probe ---
def test_disk_usage_pct_on_real_tmp_path_returns_a_sane_percent(tmp_path):
    pct, err = dpa.disk_usage_pct("tmp", str(tmp_path))
    assert err is None
    assert 0 <= pct <= 100


def test_disk_usage_pct_missing_path_is_a_probe_error():
    pct, err = dpa.disk_usage_pct("gone", "/no/such/path/wq3171")
    assert pct is None
    assert err is not None
