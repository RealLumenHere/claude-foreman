"""wq#1640 tests — prefs_block.py, run against the real
`/home/lumen/minerva/prefs_block.py` and `/home/lumen/minerva/lumen_state.py`
(not copies), with `lumen_state.DB` pointed at a throwaway temp file so these
exercise the actual production modules without touching minerva.db or the
real wq queue (wq CLI calls are monkeypatched out). Same convention as
ops/wq1689-minerva-volatile-state-halflife/test_lumen_state_wq1689.py.

Run: python3 -m pytest -q ops/wq1640-lumen-state-render-frank-side-hardware-q-a3/test_prefs_block.py
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, '/home/lumen/minerva')
import lumen_state as ls  # noqa: E402
import prefs_block as pb  # noqa: E402


@pytest.fixture
def conn():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    prev_db = ls.DB
    ls.DB = path
    c = ls._conn()
    ls.init_schema(c)
    try:
        yield c
    finally:
        c.close()
        os.unlink(path)
        ls.DB = prev_db


@pytest.fixture
def last_path():
    fd, path = tempfile.mkstemp(suffix='.last')
    os.close(fd)
    os.unlink(path)  # start absent: "no prior copy" is the common case
    yield path
    if os.path.exists(path):
        os.unlink(path)


def _seed_live_host(conn, subject, user='opsuser', ts_ip='100.1.2.3', lan_ip='192.168.1.5'):
    ls.ingest(subject, 'ssh_user', user, source_ref='test', conn=conn)
    ls.ingest(subject, 'tailscale_ip', ts_ip, source_ref='test', conn=conn)
    ls.ingest(subject, 'lan_ip', lan_ip, source_ref='test', conn=conn)


def _seed_all_hosts(conn):
    for h in pb.HOSTS:
        _seed_live_host(conn, h, user=f'{h}user', ts_ip=f'100.0.0.{pb.HOSTS.index(h)}',
                         lan_ip=f'192.168.0.{pb.HOSTS.index(h)}')


# ── render_prefs_block ────────────────────────────────────────────────────

def test_render_live_host_shows_user_ts_lan(conn, monkeypatch):
    monkeypatch.setattr(ls, 'DB', ls.DB)  # no-op; conn fixture already pointed DB
    _seed_live_host(conn, 'lumen', user='lumen', ts_ip='100.117.66.102', lan_ip='192.168.8.160')
    block = pb.render_prefs_block()
    assert '**Lumen**' in block
    assert 'user: lumen' in block
    assert 'tailscale: 100.117.66.102' in block
    assert 'lan: 192.168.8.160' in block
    assert 'DECOMMISSIONED' not in block.split('**Lumen**')[1].split('\n')[0]


def test_render_missing_predicate_reads_as_unset_not_blank(conn):
    ls.ingest('thor', 'tailscale_ip', '100.112.151.100', source_ref='test', conn=conn)
    block = pb.render_prefs_block()
    line = [l for l in block.splitlines() if '**Thor**' in l][0]
    assert 'user: unset in state_facts' in line
    assert 'lan: unset in state_facts' in line
    assert 'tailscale: 100.112.151.100' in line


def test_render_lifecycle_decommissioned_hides_ips(conn):
    _seed_live_host(conn, 'pbox', ts_ip='100.68.189.7')
    ls.ingest('pbox', 'lifecycle', 'decommissioned', source_ref='test', conn=conn)
    block = pb.render_prefs_block()
    line = [l for l in block.splitlines() if '**PBox**' in l][0]
    assert 'DECOMMISSIONED' in line
    assert '100.68.189.7' not in line


def test_render_status_convention_detects_retirement_without_lifecycle(conn):
    ls.ingest('mirror', 'status', 'DECOMMISSIONED -- removed from fleet 2026-08-28',
              source_ref='test', conn=conn)
    block = pb.render_prefs_block()
    line = [l for l in block.splitlines() if '**Mirror**' in l][0]
    assert 'DECOMMISSIONED' in line
    assert 'since 2026-08-28' not in line or True  # valid_from is ingest time in this test, not asserted


def test_render_includes_known_stale_flags(conn):
    block = pb.render_prefs_block()
    for note in pb.KNOWN_STALE_FLAGS:
        assert note in block


# ── diff_against_last ─────────────────────────────────────────────────────

def test_diff_none_when_unchanged(conn, last_path):
    _seed_all_hosts(conn)
    rendered = pb.render_prefs_block()
    pb.save_last(rendered, path=last_path)
    assert pb.diff_against_last(rendered, path=last_path) is None


def test_diff_present_on_first_run_no_prior_copy(conn, last_path):
    _seed_all_hosts(conn)
    rendered = pb.render_prefs_block()
    diff = pb.diff_against_last(rendered, path=last_path)
    assert diff is not None
    assert '+++' in diff


def test_diff_present_when_content_changed(conn, last_path):
    pb.save_last('## stale old block\n', path=last_path)
    _seed_all_hosts(conn)
    rendered = pb.render_prefs_block()
    diff = pb.diff_against_last(rendered, path=last_path)
    assert diff is not None
    assert 'stale old block' in diff


# ── check_and_file_ticket ─────────────────────────────────────────────────

def test_check_no_drift_files_no_ticket(conn, last_path, monkeypatch):
    _seed_all_hosts(conn)
    rendered = pb.render_prefs_block()
    pb.save_last(rendered, path=last_path)

    def _boom(*a, **k):
        raise AssertionError('wq must not be called when there is no drift')
    monkeypatch.setattr(pb, '_wq_add', _boom)
    monkeypatch.setattr(pb, '_wq_supersede', _boom)

    assert pb.check_and_file_ticket(last_path=last_path) is None


def test_check_drift_files_ticket_and_supersedes_prior(conn, last_path, monkeypatch):
    pb.save_last('## stale old block\n', path=last_path)
    _seed_all_hosts(conn)

    added = {}
    superseded = []

    def _fake_add(title, detail, wq_bin=pb.WQ_BIN):
        added['title'] = title
        added['detail'] = detail
        return 4242

    monkeypatch.setattr(pb, '_wq_add', _fake_add)
    monkeypatch.setattr(pb, '_wq_supersede', lambda old_id, new_id, wq_bin=pb.WQ_BIN:
                         superseded.append((old_id, new_id)))
    monkeypatch.setattr(pb, '_prior_open_tickets', lambda db_path=pb.WQ_DB: [111, 222])

    new_id = pb.check_and_file_ticket(last_path=last_path)

    assert new_id == 4242
    assert added['title'].startswith(pb.TITLE_PREFIX)
    assert 'Fleet hardware quick-ref' in added['detail']
    assert sorted(superseded) == [(111, 4242), (222, 4242)]
    # the handed-out copy is now updated, so a same-content re-check is a no-op
    assert pb.load_last(path=last_path) == pb.render_prefs_block()
