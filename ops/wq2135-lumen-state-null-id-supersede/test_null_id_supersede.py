"""wq#2135 tests -- a state_facts row with id NULL must not break supersession.

Run against the real `/home/lumen/minerva/lumen_state.py` with `lumen_state.DB`
pointed at a throwaway temp file (same shape as wq1689's tests; minerva/** is
not a git repo, see README.md).

Run: python3 -m pytest -q ops/wq2135-lumen-state-null-id-supersede/test_null_id_supersede.py
"""
import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, '/home/lumen/minerva')
import lumen_state as ls  # noqa: E402

TRIGGER = 'trg_state_facts_id_default'


@pytest.fixture
def conn():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    prev_db = ls.DB
    ls.DB = path
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    ls.init_schema(c)
    try:
        yield c
    finally:
        c.close()
        os.unlink(path)
        ls.DB = prev_db


def _legacy_null_id_row(conn, subject='nephthys', predicate='availability',
                        value='offline', valid_from='2026-09-12T06:00:14+00:00'):
    """The production shape: an open row written without an id, in a DB that
    predates the trigger."""
    conn.execute(f'DROP TRIGGER IF EXISTS {TRIGGER}')
    conn.execute(
        'INSERT INTO state_facts (subject, predicate, value, valid_from, ingested_at) '
        'VALUES (?,?,?,?,?)', (subject, predicate, value, valid_from, valid_from))
    conn.commit()
    assert conn.execute('SELECT id FROM state_facts').fetchone()['id'] is None


def test_supersede_closes_legacy_null_id_open_row(conn):
    """The wq#2135 repro: nephthys.availability open row has id NULL; superseding
    it raised UNIQUE constraint failed: state_facts.subject, state_facts.predicate."""
    _legacy_null_id_row(conn)
    r = ls.ingest('nephthys', 'availability', 'online', valid_from='2026-09-20',
                  source_ref='wq#2135-test', conn=conn)
    assert r['action'] == 'superseded'
    assert r['prior'] == 'offline'
    rows = conn.execute(
        'SELECT id, value, valid_to, closed_by FROM state_facts '
        'WHERE subject=? AND predicate=? ORDER BY valid_from', ('nephthys', 'availability')).fetchall()
    assert [x['value'] for x in rows] == ['offline', 'online']
    assert rows[0]['valid_to'] == '2026-09-20'
    assert rows[0]['closed_by'] == rows[1]['id']
    assert all(x['id'] for x in rows)
    assert sum(1 for x in rows if x['valid_to'] is None) == 1


def test_reconfirm_refreshes_legacy_null_id_row(conn):
    """Same NULL-id cause, second path: reconfirm's UPDATE ... WHERE id=? silently
    matched nothing, so last_confirmed_at never moved."""
    _legacy_null_id_row(conn)
    r = ls.ingest('nephthys', 'availability', 'offline', source_ref='wq#2135-test', conn=conn)
    assert r['action'] == 'reconfirmed'
    row = conn.execute('SELECT last_confirmed_at FROM state_facts').fetchone()
    assert row['last_confirmed_at'] is not None


def test_migrate_backfills_every_null_id(conn):
    _legacy_null_id_row(conn, predicate='availability')
    _legacy_null_id_row(conn, subject='thor', predicate='availability')
    ls._migrate(conn)
    ids = [r['id'] for r in conn.execute('SELECT id FROM state_facts')]
    assert len(ids) == 2 and all(ids) and len(set(ids)) == 2


def test_trigger_gives_idless_insert_an_id(conn):
    """After migration no writer can leave a NULL id, whatever it omits."""
    conn.execute(
        'INSERT INTO state_facts (subject, predicate, value, valid_from, ingested_at) '
        'VALUES (?,?,?,?,?)', ('thor', 'status', 'up', '2026-09-20', '2026-09-20'))
    conn.commit()
    assert conn.execute('SELECT id FROM state_facts').fetchone()['id']


def test_migrate_is_idempotent(conn):
    ls._migrate(conn)
    ls._migrate(conn)
    n = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name=?",
                     (TRIGGER,)).fetchone()[0]
    assert n == 1
