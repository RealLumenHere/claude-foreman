"""Tests for the wq#2907 WAL migration + concurrency probe. Zero-quota, all tmp.

The probe's ability to fail (wq#2630) is itself under test:
test_probe_fails_on_delete_mode runs the exact probe against a journal_mode=delete
db and requires the reader to be locked out — the 2026-09-28 incident behaviour.
"""
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import migrate_wal

PROBE = Path(__file__).parent / 'wal_probe.py'


def make_db(path, rows=50):
    c = sqlite3.connect(path)
    c.execute('CREATE TABLE notes(id INTEGER PRIMARY KEY, body TEXT)')
    c.executemany('INSERT INTO notes(body) VALUES(?)', [(f'row {i}',) for i in range(rows)])
    c.commit()
    c.close()


def journal_mode(path):
    c = sqlite3.connect(path)
    try:
        return c.execute('PRAGMA journal_mode').fetchone()[0]
    finally:
        c.close()


def probe(db, hold_secs, reader_timeout_ms=500, writer_timeout_ms=15000):
    return subprocess.run(
        [sys.executable, PROBE, '--db', str(db), '--hold-secs', str(hold_secs),
         '--reader-timeout-ms', str(reader_timeout_ms),
         '--writer-timeout-ms', str(writer_timeout_ms)],
        capture_output=True, text=True)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / 'scratch.db'
    make_db(path)
    assert journal_mode(path) == 'delete'
    return path


def test_migrate_converts_preserves_rows_and_backs_up(db, tmp_path, capsys):
    bdir = tmp_path / 'bak'
    assert migrate_wal.main(['--db', str(db), '--backup-dir', str(bdir)]) == 0
    assert journal_mode(db) == 'wal'
    c = sqlite3.connect(db)
    assert c.execute('SELECT count(*) FROM notes').fetchone()[0] == 50
    c.close()
    backups = list(bdir.glob('scratch_prewal_*.db'))
    assert len(backups) == 1
    b = sqlite3.connect(backups[0])
    assert b.execute('SELECT count(*) FROM notes').fetchone()[0] == 50
    assert b.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    b.close()


def test_migrate_is_idempotent(db, tmp_path):
    assert migrate_wal.main(['--db', str(db), '--backup-dir', str(tmp_path / 'b1')]) == 0
    assert migrate_wal.main(['--db', str(db), '--backup-dir', str(tmp_path / 'b2')]) == 0
    # second run short-circuits on "already WAL": no second backup is written
    assert not (tmp_path / 'b2').exists()


def test_dry_run_changes_nothing(db, tmp_path):
    assert migrate_wal.main(['--db', str(db), '--backup-dir', str(tmp_path / 'b'), '--dry']) == 0
    assert journal_mode(db) == 'delete'
    assert not (tmp_path / 'b').exists()


def test_failed_backup_aborts_before_touching_the_db(db, tmp_path):
    blocked = tmp_path / 'not-a-dir'
    blocked.write_text('a file where the backup dir must go')
    assert migrate_wal.main(['--db', str(db), '--backup-dir', str(blocked)]) == 4
    assert journal_mode(db) == 'delete'


def test_missing_db_fails(tmp_path):
    assert migrate_wal.main(['--db', str(tmp_path / 'nope.db')]) == 2


def test_probe_fails_on_delete_mode(db):
    """In journal_mode=delete a held write transaction locks readers out —
    the probe must detect that and exit non-zero."""
    r = probe(db, hold_secs=2)
    assert r.returncode == 1, r.stdout + r.stderr
    assert '"reader_ok": false' in r.stdout
    assert 'locked' in r.stdout


def test_probe_passes_on_wal_reader_never_blocks_writer_queues(db, tmp_path):
    assert migrate_wal.main(['--db', str(db), '--backup-dir', str(tmp_path / 'b')]) == 0
    r = probe(db, hold_secs=2)
    assert r.returncode == 0, r.stdout + r.stderr
    assert '"reader_ok": true' in r.stdout
    assert '"writer_ok": true' in r.stdout
    # probe table is cleaned up, second-writer row included
    c = sqlite3.connect(db)
    assert c.execute("SELECT count(*) FROM sqlite_master WHERE name='_wq2907_wal_probe'").fetchone()[0] == 0
    c.close()


def test_probe_writer_times_out_when_hold_outlasts_busy_timeout(db, tmp_path):
    """busy_timeout is a wait, not a guarantee: a hold longer than the writer's
    budget must surface as writer_ok=false, exit 1 — proves the probe can fail
    on the writer leg too, and documents why wq#2895's leak still needs fixing."""
    assert migrate_wal.main(['--db', str(db), '--backup-dir', str(tmp_path / 'b')]) == 0
    r = probe(db, hold_secs=3, writer_timeout_ms=500)
    assert r.returncode == 1, r.stdout + r.stderr
    assert '"reader_ok": true' in r.stdout
    assert '"writer_ok": false' in r.stdout
