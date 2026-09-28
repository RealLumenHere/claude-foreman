#!/usr/bin/env python3
"""migrate_wal.py — move a SQLite database from journal_mode=delete to WAL (wq#2907).

Why: on 2026-09-28 a single leaked BEGIN IMMEDIATE in memoryd (wq#2895) held
minerva.db's rollback-journal write lock for 35 minutes and took down every
reader AND writer fleet-wide (embed-guard, ir-triage, backup-critical, wq,
lumen_episode, lumen_state). In WAL mode readers never block on a writer and
writers queue on busy_timeout instead of raising immediately.

Order of operations (refuses to proceed past any failed step):
  1. PRAGMA quick_check must say "ok"
  2. consistent snapshot via the sqlite3 online .backup API (NOT cp: cp of a
     live db + journal can capture a torn page set)
  3. PRAGMA journal_mode=WAL  (persistent — stored in the db header)
  4. verify journal_mode == wal and quick_check == ok

Note synchronous=NORMAL (the recommended WAL pairing) is a PER-CONNECTION
pragma, not persistent: it must be set by each opener (kernel.K.connect et al —
see README follow-ups), so this script only reports it.

Usage: migrate_wal.py --db /path/to.db [--backup-dir DIR] [--dry]
Exit 0 = migrated (or already WAL). Non-zero = nothing was changed.
"""
import argparse
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LIVE_DB = '/home/lumen/minerva/minerva.db'
BACKUP_ROOT = '/home/lumen/backups'
BUSY_TIMEOUT_MS = 60_000


def connect(db, timeout_s=60):
    con = sqlite3.connect(db, timeout=timeout_s)
    con.execute(f'PRAGMA busy_timeout={BUSY_TIMEOUT_MS}')
    return con


def backup(db, backup_dir):
    dest_dir = Path(backup_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    dest = dest_dir / f'{Path(db).stem}_prewal_{stamp}.db'
    src = connect(db)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    check = sqlite3.connect(f'file:{dest}?mode=ro', uri=True)
    try:
        ok = check.execute('PRAGMA quick_check').fetchone()[0]
    finally:
        check.close()
    if ok != 'ok':
        raise RuntimeError(f'backup quick_check={ok!r} at {dest}')
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--db', default=LIVE_DB)
    ap.add_argument('--backup-dir', default=None,
                    help=f'default: {BACKUP_ROOT}/<dbstem>_wal_migration')
    ap.add_argument('--dry', action='store_true', help='report state, change nothing')
    a = ap.parse_args(argv)

    if not Path(a.db).exists():
        print(f'FAIL: {a.db} does not exist')
        return 2

    con = connect(a.db)
    try:
        mode = con.execute('PRAGMA journal_mode').fetchone()[0]
        ok = con.execute('PRAGMA quick_check').fetchone()[0]
        pages = con.execute('PRAGMA page_count').fetchone()[0]
    finally:
        con.close()
    print(f'db={a.db} journal_mode={mode} quick_check={ok} page_count={pages}')

    if ok != 'ok':
        print('FAIL: quick_check not ok — refusing to touch a corrupt db')
        return 3
    if mode == 'wal':
        print('already WAL; nothing to do')
        return 0
    if a.dry:
        print('dry run: would backup then set journal_mode=WAL')
        return 0

    backup_dir = a.backup_dir or str(Path(BACKUP_ROOT) / f'{Path(a.db).stem}_wal_migration')
    try:
        dest = backup(a.db, backup_dir)
    except (OSError, RuntimeError, sqlite3.Error) as e:
        print(f'FAIL: backup failed ({e}); db untouched')
        return 4
    print(f'backup: {dest}')

    t0 = time.monotonic()
    con = connect(a.db)
    try:
        new_mode = con.execute('PRAGMA journal_mode=WAL').fetchone()[0]
        ok = con.execute('PRAGMA quick_check').fetchone()[0]
    finally:
        con.close()
    print(f'journal_mode={new_mode} quick_check={ok} ({time.monotonic() - t0:.2f}s)')
    if new_mode != 'wal' or ok != 'ok':
        print('FAIL: migration did not take')
        return 5
    print('migrated. Reminder: synchronous=NORMAL is per-connection — set it in the '
          'shared connect helpers (see README follow-ups).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
