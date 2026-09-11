"""fleet_spof_watch.py unit tests (wq#1704).

Zero live services. alerts.db is a tmp sqlite with the fleet alerts schema, a
fake `aq` module (reads AQ_TEST_DB at call time) stands in for
agents/alert-queue/aq.py, and `systemctl` / `ntfy-send` are fake scripts whose
canned output the test controls — same convention as
ops/wq1652-cfd-actuator/tests/test_cfd_actuator.py.

Every check has a fires-on-simulated-failure test and a stays-quiet-when-healthy
test, plus an edge-clear test, because a watchdog that cannot go quiet again is
how you get atlas §5 #2 (250 open rows nobody reads).
"""
import importlib.util
import os
import sqlite3
import stat
import sys
import time

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
MODULE_DIR = os.path.dirname(TESTS_DIR)

spec = importlib.util.spec_from_file_location(
    "fleet_spof_watch", os.path.join(MODULE_DIR, "fleet_spof_watch.py"))
spof = importlib.util.module_from_spec(spec)
sys.modules["fleet_spof_watch"] = spof
spec.loader.exec_module(spof)

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

SCHEMA = """
CREATE TABLE alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT, key TEXT, title TEXT, message TEXT,
  status TEXT, count INTEGER, priority TEXT,
  first_ts TEXT, last_ts TEXT, owner TEXT, note TEXT, resolved_ts TEXT);
"""


def _script(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


class Env:
    """Handle onto the fake world: canned systemctl output + captured alerts."""

    def __init__(self, db_path, systemctl_out, ntfy_log):
        self.db_path = db_path
        self._out = systemctl_out
        self._ntfy = ntfy_log

    def systemctl_returns(self, text):
        self._out.write_text(text)

    def rows(self, status="open"):
        c = sqlite3.connect(self.db_path)
        r = c.execute("SELECT key, title, message, priority FROM alerts "
                      "WHERE source=? AND status=?",
                      (spof.SOURCE, status)).fetchall()
        c.close()
        return r

    def keys(self, status="open"):
        return sorted(k for k, _t, _m, _p in self.rows(status))

    def seed_open(self, key, title="from an earlier pass"):
        """An open finding left by a previous pass, inserted without going
        through raise_finding() so the fake ntfy-send is not involved."""
        c = sqlite3.connect(self.db_path)
        c.execute("INSERT INTO alerts(source,key,title,message,status,count,"
                  "priority) VALUES(?,?,?,?,'open',1,'high')",
                  (spof.SOURCE, key, title, title))
        c.commit()
        c.close()

    def ntfy_calls(self):
        return self._ntfy.read_text() if self._ntfy.exists() else ""


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "alerts.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setenv("AQ_TEST_DB", str(db_path))

    aq_dir = tmp_path / "aqdir"
    aq_dir.mkdir()
    (aq_dir / "aq.py").write_text(FAKE_AQ)

    # Fake systemctl: prints whatever the test staged in canned.txt. Real
    # `systemctl show` emits blank-line-separated Id=/prop= records.
    canned = tmp_path / "canned.txt"
    canned.write_text("")
    fake_systemctl = _script(
        tmp_path / "fake-systemctl",
        f"#!/usr/bin/env bash\ncat {canned}\n")

    ntfy_log = tmp_path / "ntfy.log"
    fake_ntfy = _script(
        tmp_path / "fake-ntfy",
        f'#!/usr/bin/env bash\necho "$@" >> {ntfy_log}\n')

    monkeypatch.setattr(spof, "AQ_DIR", str(aq_dir))
    monkeypatch.setattr(spof, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(spof, "SYSTEMCTL", fake_systemctl)
    monkeypatch.setattr(spof, "NTFY_BIN", fake_ntfy)
    return Env(str(db_path), canned, ntfy_log)


def _show(unit, **props):
    body = "\n".join(f"{k}={v}" for k, v in props.items())
    return f"Id={unit}\n{body}\n"


def _all_daemons(**props):
    return "\n".join(_show(d["unit"], **props) for d in spof.DAEMONS)


# --------------------------------------------------------------- daemons ---
def test_daemons_fires_when_daemon_is_dead(env, monkeypatch):
    """Simulated failure: lumen-guardian dies. Nothing else on the box notices
    — it is not in the healer table (atlas §5 #5)."""
    out = [_show(d["unit"], ActiveState="active", SubState="running",
                 MainPID="123")
           for d in spof.DAEMONS if d["unit"] != "lumen-guardian.service"]
    out.append(_show("lumen-guardian.service", ActiveState="failed",
                     SubState="failed", MainPID="0"))
    env.systemctl_returns("\n".join(out))
    monkeypatch.setattr(spof, "probe_port", lambda *a, **k: None)

    assert spof.main(["daemons"]) == 0
    assert env.keys() == ["daemon:lumen-guardian.service"]
    key, title, msg, prio = env.rows()[0]
    assert prio == "urgent"
    assert "lumen-guardian.service" in title
    assert "thermal-threshold shutdown" in msg


def test_daemons_fires_when_active_but_wedged(env, monkeypatch):
    """The failure Restart= cannot see: unit active, socket dead."""
    env.systemctl_returns(_all_daemons(
        ActiveState="active", SubState="running", MainPID="123"))
    monkeypatch.setattr(spof, "probe_port",
                        lambda *a, **k: "tcp 127.0.0.1:8784 -> TimeoutError")

    assert spof.main(["daemons"]) == 0
    assert env.keys() == ["daemon:lumen-gatus-ntfy-relay.service",
                          "daemon:ollama.service"]
    assert "Restart= will not fire" in env.rows()[0][2]


def test_daemons_quiet_and_edge_clears_when_healthy(env, monkeypatch):
    env.systemctl_returns(_all_daemons(
        ActiveState="active", SubState="running", MainPID="123"))
    monkeypatch.setattr(spof, "probe_port", lambda *a, **k: None)
    env.seed_open("daemon:ollama.service")

    assert spof.main(["daemons"]) == 0
    assert env.keys() == []
    assert env.keys("resolved") == ["daemon:ollama.service"]


# ------------------------------------------------------------- roundtrip ---
def test_roundtrip_fires_when_subscriber_never_persists(env, monkeypatch):
    """Simulated failure: alert-queue is active but wedged, so the published
    marker never reaches alerts.db."""
    monkeypatch.setattr(spof, "RT_DEADLINE_S", 0)
    assert spof.main(["roundtrip"]) == 0
    assert env.keys() == ["roundtrip:lumen-alert-queue"]
    assert "subscribe->persist BROKEN" in env.rows()[0][1]
    assert env.rows()[0][3] == "urgent"
    assert "-p min" in env.ntfy_calls()  # the probe publish itself


def test_roundtrip_fires_when_publish_fails(env, monkeypatch, tmp_path):
    monkeypatch.setattr(spof, "NTFY_BIN", _script(
        tmp_path / "broken-ntfy",
        "#!/usr/bin/env bash\necho 'all endpoints failed' >&2\nexit 1\n"))
    assert spof.main(["roundtrip"]) == 0
    assert env.keys() == ["roundtrip:lumen-alert-queue"]
    assert "publish failed" in env.rows()[0][1]


def test_roundtrip_ok_resolves_its_own_probe_row(env, monkeypatch, tmp_path):
    """A healthy subscriber: the fake ntfy-send writes the marker straight into
    alerts.db, standing in for publish -> subscribe -> persist."""
    monkeypatch.setattr(spof, "NTFY_BIN", _script(
        tmp_path / "subscribing-ntfy",
        "#!/usr/bin/env bash\n"
        "MSG=\"${@: -1}\"\n"
        f"sqlite3 {env.db_path} \"INSERT INTO alerts(source,key,title,message,"
        "status,count,priority) VALUES('ntfy:lumen-updates','rt',"
        "'spof-roundtrip','$MSG','open',1,'min')\"\n"))
    if os.system("command -v sqlite3 >/dev/null") != 0:
        pytest.skip("sqlite3 CLI not available")
    env.seed_open("roundtrip:lumen-alert-queue")

    assert spof.main(["roundtrip"]) == 0
    assert env.keys() == []
    assert env.keys("resolved") == ["roundtrip:lumen-alert-queue"]
    # the synthetic probe row must not be left open on the triage queue
    c = sqlite3.connect(env.db_path)
    left = c.execute("SELECT count(*) FROM alerts WHERE title='spof-roundtrip'"
                     " AND status='open'").fetchone()[0]
    c.close()
    assert left == 0


# -------------------------------------------------------------- restarts ---
def test_restarts_fires_on_delta_over_threshold(env, monkeypatch):
    """Simulated failure: a tunnel to a decommissioned host restart-loops."""
    monkeypatch.setattr(spof, "fleet_daemon_units",
                        lambda: ["lumen-powerdash-vmtunnel.service"])
    monkeypatch.setattr(spof, "RESTART_DELTA", 5)
    env.systemctl_returns(_show("lumen-powerdash-vmtunnel.service",
                                Restart="always", NRestarts="6"))
    assert spof.main(["restarts"]) == 0
    assert env.keys() == []  # first pass only sets the baseline

    env.systemctl_returns(_show("lumen-powerdash-vmtunnel.service",
                                Restart="always", NRestarts="42"))
    assert spof.main(["restarts"]) == 0
    assert env.keys() == ["restarts:lumen-powerdash-vmtunnel.service"]
    assert "+36" in env.rows()[0][2]
    assert "no StartLimitBurst ceiling" in env.rows()[0][2]


def test_restarts_quiet_below_threshold_and_ignores_restart_no(env, monkeypatch):
    monkeypatch.setattr(spof, "fleet_daemon_units",
                        lambda: ["a.service", "b.service"])
    monkeypatch.setattr(spof, "RESTART_DELTA", 5)
    env.systemctl_returns(_show("a.service", Restart="always", NRestarts="1")
                          + "\n"
                          + _show("b.service", Restart="no", NRestarts="99"))
    assert spof.main(["restarts"]) == 0
    env.systemctl_returns(_show("a.service", Restart="always", NRestarts="3")
                          + "\n"
                          + _show("b.service", Restart="no", NRestarts="900"))
    assert spof.main(["restarts"]) == 0
    assert env.keys() == []


# ---------------------------------------------------------------- timers ---
def _timer_block(now, ages, result="success"):
    """ages: {timer: seconds-since-last-trigger}. Emits the timer records then
    the service records, in the two batches unit_props() asks for."""
    out = []
    for t in spof.TIMERS:
        age = ages.get(t["timer"], 0)
        out.append(_show(t["timer"], LastTriggerUSec=f"@{now - age}"))
    for t in spof.TIMERS:
        svc = t["timer"][: -len(".timer")] + ".service"
        out.append(_show(svc, Result=result, ExecMainExitTimestamp=f"@{now}"))
    return "\n".join(out)


def test_timers_fires_when_vault_ha_monitor_stops(env):
    """Simulated failure: wadjet-ha-monitor.timer is stopped. Atlas §4.12 —
    'notices: nobody'. Frank's password manager rides this."""
    now = int(time.time())
    env.systemctl_returns(_timer_block(
        now, {"wadjet-ha-monitor.timer": 3600}))
    assert spof.main(["timers"]) == 0
    assert env.keys() == ["timer:wadjet-ha-monitor.timer"]
    key, title, msg, prio = env.rows()[0]
    assert prio == "urgent"
    assert "has not fired" in title
    assert "vault primary unwatched" in msg


def test_timers_fires_when_backup_timer_fires_but_service_fails(env):
    """Ran-recently and succeeded are different assertions (ISSUE-LOG #44):
    a trigger-recency check alone calls this healthy."""
    now = int(time.time())
    env.systemctl_returns(_timer_block(now, {}, result="exit-code"))
    assert spof.main(["timers"]) == 0
    assert env.keys() == sorted(f"timer:{t['timer']}" for t in spof.TIMERS)
    assert "fires but" in env.rows()[0][1]


def test_timers_fires_when_timer_is_not_loaded(env):
    """Simulated failure: the timer was lost to a `unit-drift apply`."""
    now = int(time.time())
    kept = [b for b in _timer_block(now, {}).split("\n\n")
            if "wadjet-ha-monitor.timer" not in b]
    env.systemctl_returns("\n\n".join(kept))
    assert spof.main(["timers"]) == 0
    assert env.keys() == ["timer:wadjet-ha-monitor.timer"]
    assert "not loaded" in env.rows()[0][1]


def test_timers_quiet_and_edge_clears_when_all_healthy(env):
    now = int(time.time())
    env.systemctl_returns(_timer_block(now, {}))
    env.seed_open("timer:wadjet-ha-monitor.timer")
    assert spof.main(["timers"]) == 0
    assert env.keys() == []
    assert env.keys("resolved") == ["timer:wadjet-ha-monitor.timer"]


# ----------------------------------------------------------------- shape ---
def test_every_check_exits_zero_on_a_finding(env, monkeypatch):
    """wq#1657 contract: a finding is not a unit failure, so OnFailure= on
    these units means 'the guard crashed', never 'the guard found something'."""
    env.systemctl_returns(_all_daemons(ActiveState="failed", SubState="failed",
                                       MainPID="0"))
    monkeypatch.setattr(spof, "RT_DEADLINE_S", 0)
    monkeypatch.setattr(spof, "fleet_daemon_units", lambda: [])
    for cmd in ("daemons", "roundtrip", "restarts", "timers"):
        assert spof.main([cmd]) == 0, cmd
    assert len(env.rows()) > 0
