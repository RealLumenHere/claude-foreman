"""wq#3735: litellm-auth-alert closes its own burst-401 aq row once 401s fall
below threshold. aq#2390 sat open at x109 after the last burst because the
alerter only ever bumped it.

Runs the real script against a temp alerts db through a fake `aq` module, so
the live alert queue is never touched. LITELLM_AUTH_ALERT_SCRIPT points the
suite at another copy (e.g. the pre-fix deployed one, which must fail
test_below_threshold_resolves_open_and_acked_burst_rows).
"""
import datetime
import json
import os
import sqlite3
import subprocess
from pathlib import Path

SCRIPT = os.environ.get("LITELLM_AUTH_ALERT_SCRIPT",
                        str(Path(__file__).resolve().parent.parent / "litellm-auth-alert"))

FAKE_AQ_MODULE = """
import os, sqlite3
def db():
    return sqlite3.connect(os.environ["FAKE_AQ_DB"])
def now():
    return "T"
"""

FAKE_AQ_BIN = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_AQ_CALLS"
"""


def _setup(tmp_path, n_401):
    dbp = tmp_path / "alerts.db"
    c = sqlite3.connect(dbp)
    c.execute("CREATE TABLE alerts (id INTEGER PRIMARY KEY, source TEXT, key TEXT, "
              "status TEXT, note TEXT, resolved_ts TEXT)")
    c.executemany("INSERT INTO alerts (id, source, key, status) VALUES (?,?,?,?)", [
        (2390, "litellm-auth", "burst-401", "open"),
        (2, "litellm-auth", "burst-401", "acked"),
        (3, "litellm-auth", "other-key", "open"),
        (4, "litellm-completion", "burst-401", "open"),
    ])
    c.commit()
    c.close()

    aq_dir = tmp_path / "aqmod"
    aq_dir.mkdir()
    (aq_dir / "aq.py").write_text(FAKE_AQ_MODULE)
    aq_bin = tmp_path / "aq"
    aq_bin.write_text(FAKE_AQ_BIN)
    aq_bin.chmod(0o755)

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log = tmp_path / "auth-failures.jsonl"
    log.write_text("".join(json.dumps({"ts": now, "event": "auth_401", "endpoint": "GET /v1/models",
                                       "raw": "INFO:     172.20.0.1:1 - x"}) + "\n"
                           for _ in range(n_401)))

    env = dict(os.environ,
               LITELLM_AUTH_LOGFILE=str(log),
               LITELLM_AUTH_ALERT_STATE_DIR=str(tmp_path / "state"),
               LITELLM_AUTH_ALERT_AQ=str(aq_bin),
               LITELLM_AUTH_ALERT_AQ_DIR=str(aq_dir),
               LITELLM_AUTH_ALERT_NTFY=str(tmp_path / "no-ntfy"),
               FAKE_AQ_DB=str(dbp),
               FAKE_AQ_CALLS=str(tmp_path / "aq-calls"))
    return dbp, env


def _run(env):
    r = subprocess.run(["bash", SCRIPT, "--print"], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _statuses(dbp):
    return dict(sqlite3.connect(dbp).execute("SELECT id, status FROM alerts"))


def test_below_threshold_resolves_open_and_acked_burst_rows(tmp_path):
    dbp, env = _setup(tmp_path, n_401=0)
    assert "breached=false" in _run(env)
    assert _statuses(dbp) == {2390: "resolved", 2: "resolved", 3: "open", 4: "open"}
    note, ts = sqlite3.connect(dbp).execute(
        "SELECT note, resolved_ts FROM alerts WHERE id=2390").fetchone()
    assert note.startswith("auto-cleared: 0 401s") and ts == "T"


def test_breach_bumps_row_and_never_resolves(tmp_path):
    dbp, env = _setup(tmp_path, n_401=6)
    assert "breached=true" in _run(env)
    assert _statuses(dbp) == {2390: "open", 2: "acked", 3: "open", 4: "open"}
    assert (tmp_path / "aq-calls").read_text().startswith("add litellm-auth burst-401 ")


def test_unreadable_aq_db_stays_fail_open(tmp_path):
    dbp, env = _setup(tmp_path, n_401=0)
    env["LITELLM_AUTH_ALERT_AQ_DIR"] = str(tmp_path / "missing")
    assert "breached=false" in _run(env)
    assert _statuses(dbp)[2390] == "open"
