"""aer_storm_alarm.py unit tests (wq#2851).

Zero live services and zero real ssh: alerts.db is a tmp sqlite with the fleet
alerts schema, a fake `aq` module (same convention as
ops/wq1652-cfd-actuator/tests/test_cfd_actuator.py and
ops/wq1704-missing-watchdogs) stands in for agents/alert-queue/aq.py, ntfy-send
is a logging fake, and `check()`'s ssh probe is replaced with a synthetic
`prober(node, host, window_min) -> (count, error)` function so the AER counts
that would come from a real journalctl over ssh can be injected directly —
this is the "synthetic injection" the ticket's test-to-failure asks for.
"""
import importlib.util
import json
import os
import sqlite3
import stat
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.dirname(TESTS_DIR)

spec = importlib.util.spec_from_file_location(
    "aer_storm_alarm", os.path.join(MODULE_DIR, "aer_storm_alarm.py"))
aer = importlib.util.module_from_spec(spec)
sys.modules["aer_storm_alarm"] = aer
spec.loader.exec_module(aer)

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
    monkeypatch.setattr(aer, "AQ_DIR", str(aq_dir))
    monkeypatch.setattr(aer, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(aer, "NTFY_BIN", fake_ntfy)

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


RULE = {"window_min": 15, "raise_above": 30, "clear_at_or_below": 15}
CFG = {"rule": RULE, "nodes": {"nephthys": "nephthys"}}


def const_prober(count, error=None):
    def _p(node, host, window_min):
        return count, error
    return _p


# ------------------------------------------------------------- next_state ---
def test_next_state_raises_above_threshold():
    assert aer.next_state(False, 31, RULE) is True
    assert aer.next_state(False, 30, RULE) is False


def test_next_state_hysteresis_does_not_clear_mid_band():
    # firing, count drops to 20: below raise_above but above clear_at_or_below -> stays firing
    assert aer.next_state(True, 20, RULE) is True
    assert aer.next_state(True, 15, RULE) is False


def test_next_state_probe_failure_carries_prior_state():
    assert aer.next_state(True, None, RULE) is True
    assert aer.next_state(False, None, RULE) is False


# ------------------------------------------------------------------ check ---
def test_check_raises_one_aq_row_and_one_ntfy(env):
    verdicts = aer.check(CFG, prober=const_prober(45))
    assert verdicts[0]["firing"] is True
    rows = env.rows(source="aer-storm", key="aer-storm:nephthys", status="open")
    assert len(rows) == 1
    assert any("AER storm" in ln for ln in env.ntfy_lines())


def test_check_sustained_firing_does_not_re_raise_or_spam(env):
    """Two consecutive passes above threshold: only the FIRST is a state
    transition. The second must not add a second aq row or a second ntfy —
    this is the "no per-minute spam" requirement."""
    aer.check(CFG, prober=const_prober(45))
    aer.check(CFG, prober=const_prober(60))
    rows = env.rows(source="aer-storm", key="aer-storm:nephthys", status="open")
    assert len(rows) == 1
    ntfy_calls = [ln for ln in env.ntfy_lines() if "AER storm:" in ln and "RECOVERED" not in ln]
    assert len(ntfy_calls) == 1


def test_check_below_threshold_never_fires(env):
    for _ in range(3):
        verdicts = aer.check(CFG, prober=const_prober(5))
    assert verdicts[0]["firing"] is False
    assert env.rows(source="aer-storm") == []


def test_check_clears_and_resolves_row_on_recovery(env):
    aer.check(CFG, prober=const_prober(45))
    assert len(env.rows(source="aer-storm", status="open")) == 1
    verdicts = aer.check(CFG, prober=const_prober(0))
    assert verdicts[0]["firing"] is False
    assert env.rows(source="aer-storm", status="open") == []
    resolved = env.rows(source="aer-storm", status="resolved")
    assert len(resolved) == 1
    assert any("RECOVERED" in ln for ln in env.ntfy_lines())


def test_check_dry_run_never_acts(env):
    verdicts = aer.check(CFG, dry_run=True, prober=const_prober(999))
    assert verdicts[0]["firing"] is True
    assert env.rows(source="aer-storm") == []
    assert env.ntfy_lines() == []


def test_check_probe_error_does_not_flap_a_firing_node(env):
    aer.check(CFG, prober=const_prober(45))
    aer.check(CFG, prober=const_prober(None, error="ssh timeout"))
    rows = env.rows(source="aer-storm", status="open")
    assert len(rows) == 1  # still firing, no spurious clear


# ----------------------------------------------------------------- config ---
def test_validate_rejects_inverted_hysteresis():
    bad = {"rule": {"window_min": 15, "raise_above": 10, "clear_at_or_below": 20},
           "nodes": {"isis": "isis"}}
    with pytest.raises(aer.ConfigError):
        aer.validate(bad)


def test_validate_rejects_empty_nodes():
    bad = {"rule": dict(RULE), "nodes": {}}
    with pytest.raises(aer.ConfigError):
        aer.validate(bad)


def test_default_config_has_all_four_macpro61_nodes():
    cfg = aer.default_config()
    assert set(cfg["nodes"]) == {"isis", "hathor", "khepri", "nephthys"}


# ----------------------------------------------------------------- replay ---
def test_replay_fires_on_a_dense_burst(tmp_path):
    """Synthetic journal: 40 PCIe Bus Error lines inside one 15-minute span —
    the shape of the real boot -1/-3/-5 storms (bursty, not uniform hourly)."""
    log = tmp_path / "boot-died.log"
    lines = []
    for i in range(40):
        second = i * 20  # 40 lines, 20s apart -> spans 800s (~13.3min), inside one 15min window
        minute, sec = 6 + second // 60, second % 60
        lines.append(f"2026-09-27T04:{minute:02d}:{sec:02d}+0000 kernel: PCIe Bus Error: id=0001")
    log.write_text("\n".join(lines) + "\n")
    out = aer.replay(str(log), "nephthys", RULE)
    assert any(e["state"] == "raise" for e in out)


def test_replay_healthy_boot_never_fires(tmp_path):
    """8 lines spread across 22 hours (boot 0's healthy control, max 5/hr) never
    crosses raise_above=30 in any 15-minute window."""
    log = tmp_path / "boot-healthy.log"
    lines = [f"2026-09-27T{h:02d}:00:00+0000 kernel: PCIe Bus Error: id=0001" for h in range(8)]
    log.write_text("\n".join(lines) + "\n")
    out = aer.replay(str(log), "nephthys", RULE)
    assert out == []


def test_bucket_hourly_groups_by_hour():
    text = ("2026-09-20T09:05:00+0000 kernel: PCIe Bus Error: x\n"
            "2026-09-20T09:55:00+0000 kernel: PCIe Bus Error: x\n"
            "2026-09-20T10:01:00+0000 kernel: PCIe Bus Error: x\n"
            "2026-09-20T10:01:00+0000 something unrelated\n")
    buckets = aer.bucket_hourly(text)
    assert buckets == {"2026-09-20T09": 2, "2026-09-20T10": 1}
