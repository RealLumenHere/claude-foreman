#!/usr/bin/env python3
"""wal_probe.py — falsifiable concurrency probe for the wq#2907 WAL migration.

Re-creates the 2026-09-28 incident shape: one process holds a write transaction
(BEGIN EXCLUSIVE + a real INSERT) on the target db for --hold-secs, while the
probe asserts the two properties WAL is supposed to buy:

  reader_ok  a second connection (file:...?mode=ro) can still SELECT during the
             hold. In journal_mode=delete this FAILS ("database is locked") —
             run the probe on a delete-mode db and watch it exit 1; that is the
             probe's ability to fail (wq#2630).
  writer_ok  a second writer with busy_timeout waits out the hold and then
             commits, instead of raising. Requires hold < --writer-timeout-ms.

Writes go to a scratch table _wq2907_wal_probe which is dropped afterwards.

Usage: wal_probe.py --db PATH [--hold-secs 10] [--reader-timeout-ms 2000]
                    [--writer-timeout-ms 30000]
Exit 0 = both properties held. 1 = at least one failed (delete-mode behaviour).
"""
import argparse
import json
import sqlite3
import subprocess
import sys
import time

TABLE = '_wq2907_wal_probe'


def holder(db, hold_secs):
    """Child-process role: take the write lock, hold it, commit, exit."""
    con = sqlite3.connect(db, timeout=30)
    con.execute('PRAGMA busy_timeout=30000')
    con.execute('BEGIN EXCLUSIVE')
    con.execute(f'INSERT INTO {TABLE}(role, ts) VALUES(?, ?)', ('holder', time.time()))
    print('HOLDING', flush=True)
    time.sleep(hold_secs)
    con.execute('COMMIT')
    con.close()
    print('RELEASED', flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--db', required=True)
    ap.add_argument('--hold-secs', type=float, default=10)
    ap.add_argument('--reader-timeout-ms', type=int, default=2000)
    ap.add_argument('--writer-timeout-ms', type=int, default=30000)
    ap.add_argument('--hold-role', action='store_true', help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    if a.hold_role:
        holder(a.db, a.hold_secs)
        return 0

    setup = sqlite3.connect(a.db, timeout=30)
    setup.execute(f'CREATE TABLE IF NOT EXISTS {TABLE}(role TEXT, ts REAL)')
    setup.commit()
    setup.close()

    verdict = {'db': a.db, 'hold_secs': a.hold_secs, 'reader_ok': False,
               'writer_ok': False, 'reader_error': None, 'writer_error': None,
               'writer_wait_s': None}
    child = subprocess.Popen(
        [sys.executable, __file__, '--hold-role', '--db', a.db,
         '--hold-secs', str(a.hold_secs)],
        stdout=subprocess.PIPE, text=True)
    try:
        line = child.stdout.readline().strip()
        if line != 'HOLDING':
            raise RuntimeError(f'holder never took the lock (got {line!r})')

        # Reader during the hold: never blocks in WAL, "database is locked"
        # in delete mode once a writer holds the journal's exclusive lock.
        try:
            ro = sqlite3.connect(f'file:{a.db}?mode=ro', uri=True,
                                 timeout=a.reader_timeout_ms / 1000)
            try:
                ro.execute('SELECT count(*) FROM sqlite_master').fetchone()
                verdict['reader_ok'] = True
            finally:
                ro.close()
        except sqlite3.OperationalError as e:
            verdict['reader_error'] = str(e)

        # Second writer during the hold: must wait out busy_timeout and land.
        t0 = time.monotonic()
        try:
            w = sqlite3.connect(a.db, timeout=a.writer_timeout_ms / 1000)
            w.execute(f'PRAGMA busy_timeout={a.writer_timeout_ms}')
            try:
                w.execute(f'INSERT INTO {TABLE}(role, ts) VALUES(?, ?)',
                          ('second_writer', time.time()))
                w.commit()
                verdict['writer_ok'] = True
            finally:
                w.close()
        except sqlite3.OperationalError as e:
            verdict['writer_error'] = str(e)
        verdict['writer_wait_s'] = round(time.monotonic() - t0, 2)
    finally:
        child.wait(timeout=a.hold_secs + 60)

    cleanup = sqlite3.connect(a.db, timeout=60)
    cleanup.execute('PRAGMA busy_timeout=60000')
    cleanup.execute(f'DROP TABLE IF EXISTS {TABLE}')
    cleanup.commit()
    cleanup.close()

    print(json.dumps(verdict, indent=1))
    return 0 if verdict['reader_ok'] and verdict['writer_ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
