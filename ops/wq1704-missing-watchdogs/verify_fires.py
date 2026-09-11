#!/usr/bin/env python3
"""wq#1704 — drive each watchdog into its failure branch and show the alert.

    python3 ops/wq1704-missing-watchdogs/verify_fires.py

The ticket asks for "verify each fires on simulated failure". Simulating the
real failure would mean stopping lumen-guardian (hardware shutdown protection),
wedging lumen-alert-queue, or masking the vault HA timer — none of which a build
lane may do on a live box. So each check is instead pointed at a target that
genuinely cannot pass, against a throwaway alerts.db and a fake ntfy-send:

  daemons    -> a unit name that does not exist (same code path as a dead one:
                ActiveState is not 'active')
  roundtrip  -> real publish, zero-second deadline, so the marker cannot land
  restarts   -> a seeded baseline that makes the live NRestarts a huge delta
  timers     -> a one-second freshness budget on the real vault HA timer

Exit 0 only if all four raised the finding they are supposed to raise.
"""
import importlib.util
import os
import sqlite3
import stat
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

spec = importlib.util.spec_from_file_location(
    "fleet_spof_watch", os.path.join(HERE, "fleet_spof_watch.py"))
spof = importlib.util.module_from_spec(spec)
sys.modules["fleet_spof_watch"] = spof
spec.loader.exec_module(spof)

FAKE_AQ = '''
import os, sqlite3, time

def db():
    return sqlite3.connect(os.environ["SPOF_SMOKE_DB"])

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def ingest(source, key, title, message, priority):
    c = db()
    c.execute("INSERT INTO alerts(source,key,title,message,status,count,"
              "priority,first_ts,last_ts) VALUES(?,?,?,?,'open',1,?,?,?)",
              (source, key, title, message, priority, now(), now()))
    c.commit(); c.close()
'''

SCHEMA = """CREATE TABLE alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT, key TEXT, title TEXT, message TEXT,
  status TEXT, count INTEGER, priority TEXT,
  first_ts TEXT, last_ts TEXT, owner TEXT, note TEXT, resolved_ts TEXT);"""


def sandbox():
    tmp = tempfile.mkdtemp(prefix="spof-verify-")
    aq_dir = os.path.join(tmp, "aqdir")
    os.makedirs(aq_dir)
    with open(os.path.join(aq_dir, "aq.py"), "w") as f:
        f.write(FAKE_AQ)
    db_path = os.path.join(tmp, "alerts.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    ntfy = os.path.join(tmp, "fake-ntfy")
    with open(ntfy, "w") as f:
        f.write("#!/usr/bin/env bash\nexit 0\n")
    os.chmod(ntfy, os.stat(ntfy).st_mode | stat.S_IEXEC | stat.S_IXOTH)

    os.environ["SPOF_SMOKE_DB"] = db_path
    spof.AQ_DIR = aq_dir
    spof.STATE_DIR = os.path.join(tmp, "state")
    spof.NTFY_BIN = ntfy
    return db_path


def open_rows(db_path):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT key, priority, title FROM alerts WHERE status='open'"
    ).fetchall()
    conn.close()
    return rows


# setup() rebinds module globals; each run must start from the shipped config.
PATCHABLE = ("DAEMONS", "TIMERS", "RT_DEADLINE_S", "RESTART_DELTA",
             "fleet_daemon_units", "unit_props")
ORIGINALS = {n: getattr(spof, n) for n in PATCHABLE}


def run(name, want_key_prefix, setup):
    for n, v in ORIGINALS.items():
        setattr(spof, n, v)
    db_path = sandbox()
    setup()
    rc = spof.main([name])
    rows = open_rows(db_path)
    hit = [r for r in rows if r[0].startswith(want_key_prefix)]
    ok = rc == 0 and bool(hit)
    print(f"\n[{'FIRED' if ok else 'DID NOT FIRE'}] {name} (exit {rc})")
    for key, prio, title in hit:
        print(f"    [{prio}] {key}")
        print(f"    {title}")
    return ok


def main():
    results = []

    def dead_unit():
        spof.DAEMONS = [{"unit": "lumen-spof-verify-nonexistent.service",
                         "port": None,
                         "silent": "simulated: a watched daemon is gone"}]
    results.append(run("daemons", "daemon:", dead_unit))

    def impossible_deadline():
        spof.RT_DEADLINE_S = 0
    results.append(run("roundtrip", "roundtrip:", impossible_deadline))

    def restart_storm():
        # A restart loop cannot be induced on a live box without breaking a
        # real daemon's dependency, so the unit is synthetic; the delta
        # arithmetic and the alert it produces are the real code path.
        unit = "lumen-spof-verify-loop.service"
        spof.fleet_daemon_units = lambda: [unit]
        spof.unit_props = lambda units, props: {
            unit: {"Id": unit, "Restart": "always", "NRestarts": "47"}}
        spof.save_state("restarts.json", {"baseline": {unit: 2},
                                          "since": int(time.time()),
                                          "last": {}})
    results.append(run("restarts", "restarts:", restart_storm))

    def impossible_freshness():
        spof.TIMERS = [{"timer": "wadjet-ha-monitor.timer", "max_age_s": 1,
                        "why": "simulated: vault primary unwatched"}]
    results.append(run("timers", "timer:", impossible_freshness))

    print(f"\n{sum(results)}/4 watchdogs fired on simulated failure")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
