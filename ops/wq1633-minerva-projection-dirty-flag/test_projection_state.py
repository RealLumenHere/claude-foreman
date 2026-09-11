"""wq#1633 tests. Schema self-heals (ensure_schema), so tests build a bare
:memory: db (plus a minimal state_facts for the freshness-check tests) and
never hand-roll projection_state's own schema."""

import os
import sqlite3
import subprocess
import sys

import pytest

import projection_state as ps

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    return c


def test_never_regenerated_reads_dirty(conn):
    assert ps.is_dirty(conn, 'live_fleet_state') is True


def test_mark_then_clear(conn):
    ps.mark_dirty(conn, ['live_fleet_state'])
    conn.commit()
    assert ps.is_dirty(conn, 'live_fleet_state') is True

    ps.clear_dirty(conn, ['live_fleet_state'], ts='2026-09-11T00:00:00+00:00')
    assert ps.is_dirty(conn, 'live_fleet_state') is False
    assert ps.last_regen_at(conn, 'live_fleet_state') == '2026-09-11T00:00:00+00:00'


def test_mark_state_projections_dirty_covers_both(conn):
    ps.clear_dirty(conn, ps.STATE_PROJECTIONS, ts='2026-09-01T00:00:00+00:00')
    ps.mark_state_projections_dirty(conn)
    conn.commit()
    assert ps.any_dirty(conn, ps.STATE_PROJECTIONS) is True
    assert ps.is_dirty(conn, 'live_fleet_state') is True
    assert ps.is_dirty(conn, 'lumen_state_txt') is True


def test_mark_dirty_same_transaction_as_a_fact_write(conn):
    """Mirrors the lumen_state.py _ingest() hook: a fact write and the dirty
    flag land in one transaction, so a rollback undoes both."""
    conn.execute('CREATE TABLE state_facts (id TEXT PRIMARY KEY, value TEXT)')
    ps.clear_dirty(conn, ps.STATE_PROJECTIONS, ts='2026-09-01T00:00:00+00:00')

    conn.execute("INSERT INTO state_facts VALUES ('f1', 'x')")
    ps.mark_state_projections_dirty(conn)
    conn.rollback()

    assert conn.execute('SELECT COUNT(*) FROM state_facts').fetchone()[0] == 0
    assert ps.is_dirty(conn, 'live_fleet_state') is False  # rolled back too

    conn.execute("INSERT INTO state_facts VALUES ('f2', 'y')")
    ps.mark_state_projections_dirty(conn)
    conn.commit()
    assert ps.is_dirty(conn, 'live_fleet_state') is True


def test_clear_is_idempotent(conn):
    ps.clear_dirty(conn, ['live_fleet_state'], ts='2026-09-01T00:00:00+00:00')
    ps.clear_dirty(conn, ['live_fleet_state'], ts='2026-09-02T00:00:00+00:00')
    assert ps.last_regen_at(conn, 'live_fleet_state') == '2026-09-02T00:00:00+00:00'


def test_generated_from_header_md_and_txt():
    md = ps.generated_from_header('state_facts', ts='2026-09-11T00:00:00+00:00', fmt='md')
    txt = ps.generated_from_header('state_facts', ts='2026-09-11T00:00:00+00:00', fmt='txt')
    assert md == '<!-- generated-from: state_facts@2026-09-11T00:00:00+00:00 -->'
    assert txt == '# generated-from: state_facts@2026-09-11T00:00:00+00:00'


def test_parse_generated_from_roundtrip():
    for fmt in ('md', 'txt'):
        header = ps.generated_from_header('state_facts', ts='2026-09-11T00:00:00+00:00', fmt=fmt)
        store, ts = ps.parse_generated_from(header + '\nbody\n')
        assert store == 'state_facts'
        assert ts == '2026-09-11T00:00:00+00:00'


def test_parse_generated_from_missing_header():
    store, ts = ps.parse_generated_from('# LIVE FLEET STATE\nsome content\n')
    assert (store, ts) == (None, None)


def test_stamp_file_prepends_and_is_idempotent(tmp_path):
    md = tmp_path / 'LIVE_FLEET_STATE.md'
    md.write_text('## LIVE FLEET STATE\nbody line\n')

    ps.stamp_file(str(md), 'state_facts', ts='2026-09-11T00:00:00+00:00')
    text = md.read_text()
    assert text.startswith('<!-- generated-from: state_facts@2026-09-11T00:00:00+00:00 -->\n')
    assert '## LIVE FLEET STATE\nbody line\n' in text

    # re-stamp: exactly one header line, body untouched
    ps.stamp_file(str(md), 'state_facts', ts='2026-09-11T00:05:00+00:00')
    text = md.read_text()
    assert text.count('generated-from:') == 1
    assert '00:05:00' in text.splitlines()[0]
    assert text.splitlines()[1:] == ['## LIVE FLEET STATE', 'body line']


def test_stamp_file_txt_uses_hash_comment(tmp_path):
    f = tmp_path / 'lumen_state.txt'
    f.write_text('ground truth\n')
    ps.stamp_file(str(f), 'state_facts', ts='2026-09-11T00:00:00+00:00')
    assert f.read_text().splitlines()[0] == '# generated-from: state_facts@2026-09-11T00:00:00+00:00'


def test_freshness_warn_true_when_header_older():
    assert ps.freshness_warn('2026-09-01T00:00:00+00:00', '2026-09-10T00:00:00+00:00') is True


def test_freshness_warn_false_when_header_newer_or_equal():
    assert ps.freshness_warn('2026-09-10T00:00:00+00:00', '2026-09-01T00:00:00+00:00') is False
    assert ps.freshness_warn('2026-09-10T00:00:00+00:00', '2026-09-10T00:00:00+00:00') is False


def test_freshness_warn_missing_inputs_never_warns():
    assert ps.freshness_warn(None, '2026-09-10T00:00:00+00:00') is False
    assert ps.freshness_warn('2026-09-10T00:00:00+00:00', None) is False


def test_max_ingested_at(conn):
    conn.execute('CREATE TABLE state_facts (ingested_at TEXT)')
    assert ps.max_ingested_at(conn) is None
    conn.execute("INSERT INTO state_facts VALUES ('2026-09-01T00:00:00+00:00')")
    conn.execute("INSERT INTO state_facts VALUES ('2026-09-05T00:00:00+00:00')")
    assert ps.max_ingested_at(conn) == '2026-09-05T00:00:00+00:00'


def _run_cli(args, db):
    return subprocess.run(
        [sys.executable, os.path.join(HERE, 'projection_state.py'), '--db', db, *args],
        capture_output=True, text=True)


def test_cli_is_dirty_exit_codes(tmp_path):
    db = str(tmp_path / 'minerva.db')
    # never regenerated -> dirty -> exit 0
    r = _run_cli(['is-dirty', 'live_fleet_state'], db)
    assert r.returncode == 0
    assert 'dirty' in r.stdout

    r = _run_cli(['clear', 'live_fleet_state'], db)
    assert r.returncode == 0

    r = _run_cli(['is-dirty', 'live_fleet_state'], db)
    assert r.returncode == 1
    assert 'clean' in r.stdout


def test_cli_mark_dirty_then_status_json(tmp_path):
    db = str(tmp_path / 'minerva.db')
    _run_cli(['clear', 'live_fleet_state', 'lumen_state_txt'], db)
    _run_cli(['mark-dirty', 'live_fleet_state'], db)
    r = _run_cli(['status', '--json'], db)
    assert r.returncode == 0
    assert '"live_fleet_state"' in r.stdout
    assert '"dirty": 1' in r.stdout
    assert '"dirty": 0' in r.stdout  # lumen_state_txt stayed clean


def test_cli_check_warn_stale(tmp_path):
    db = str(tmp_path / 'minerva.db')
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE state_facts (ingested_at TEXT)')
    conn.execute("INSERT INTO state_facts VALUES ('2026-09-10T00:00:00+00:00')")
    conn.commit()
    conn.close()

    md = tmp_path / 'LIVE_FLEET_STATE.md'
    md.write_text('<!-- generated-from: state_facts@2026-09-01T00:00:00+00:00 -->\nbody\n')

    r = _run_cli(['check-warn', 'live_fleet_state', '--file', str(md)], db)
    assert r.returncode == 0
    assert 'WARN' in r.stdout
    assert '2026-09-01T00:00:00+00:00' in r.stdout


def test_cli_check_warn_fresh_prints_nothing(tmp_path):
    db = str(tmp_path / 'minerva.db')
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE state_facts (ingested_at TEXT)')
    conn.execute("INSERT INTO state_facts VALUES ('2026-09-01T00:00:00+00:00')")
    conn.commit()
    conn.close()

    md = tmp_path / 'LIVE_FLEET_STATE.md'
    md.write_text('<!-- generated-from: state_facts@2026-09-10T00:00:00+00:00 -->\nbody\n')

    r = _run_cli(['check-warn', 'live_fleet_state', '--file', str(md)], db)
    assert r.returncode == 0
    assert r.stdout.strip() == ''
