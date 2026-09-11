#!/usr/bin/env python3
"""wq#1704 live smoke — runs the read-only checks against REAL systemd but
against a throwaway alerts.db and a fake ntfy-send, so a smoke run can neither
page Frank nor add rows to the live triage queue.

    python3 ops/wq1704-missing-watchdogs/smoke.py

The unit tests prove the logic against canned `systemctl show` output; this
proves the canned output is the shape this box actually emits.

`roundtrip` is deliberately not smoked here: it publishes to a live ntfy topic
and must assert against the real alerts.db, so install.sh verifies it instead.
"""
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

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


def main():
    tmp = tempfile.mkdtemp(prefix="spof-smoke-")
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
        f.write("#!/usr/bin/env bash\necho \"[fake ntfy] $*\"\n")
    os.chmod(ntfy, os.stat(ntfy).st_mode | stat.S_IEXEC | stat.S_IXOTH)

    env = dict(os.environ,
               SPOF_SMOKE_DB=db_path, SPOF_AQ_DIR=aq_dir,
               SPOF_STATE_DIR=os.path.join(tmp, "state"), SPOF_NTFY_BIN=ntfy)

    rc = 0
    for check in ("daemons", "timers", "restarts"):
        print(f"== {check} ==")
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "fleet_spof_watch.py"), check],
            env=env, capture_output=True, text=True, timeout=300)
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
        print(f"-- exit {r.returncode} (0 expected even on findings: wq#1657)")
        rc |= r.returncode

    print("== findings raised against the throwaway db ==")
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT key, priority, title FROM alerts").fetchall()
    conn.close()
    if not rows:
        print("  (none — every watched SPOF is healthy right now)")
    for k, p, t in rows:
        print(f"  [{p}] {k}: {t}")
    print(f"\nthrowaway db: {db_path} (live alerts.db untouched)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
