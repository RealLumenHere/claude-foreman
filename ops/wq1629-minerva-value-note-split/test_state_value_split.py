"""wq#1629 tests — value/note split at ingest, and the backfill that applies it
to facts already stored.

Run against the real `/home/lumen/minerva/lumen_state.py` (the deployed module,
not a copy) with `lumen_state.DB` pointed at a throwaway temp file, so these
exercise production code without touching minerva.db — same shape as
`ops/wq1689-minerva-volatile-state-halflife/test_lumen_state_wq1689.py`.

Run: python3 -m pytest -q ops/wq1629-minerva-value-note-split/test_state_value_split.py
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, '/home/lumen/minerva')
import lumen_state as ls  # noqa: E402

# The repo copy is the source of truth for the splitter; /home/lumen/minerva
# holds the deployed copy that lumen_state imports. Load the repo one by path so
# a test failure here means the committed file is wrong.
_spec = importlib.util.spec_from_file_location(
    'svs_repo', os.path.join(HERE, 'state_value_split.py'))
svs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(svs)

_scan_spec = importlib.util.spec_from_file_location(
    'lcs', os.path.join(os.path.dirname(HERE),
                        'wq1609-minerva-contradiction-scan',
                        'lumen_contradiction_scan.py'))
lcs = importlib.util.module_from_spec(_scan_spec)
_scan_spec.loader.exec_module(lcs)


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


def _row(conn, subject, predicate):
    return conn.execute(
        'SELECT * FROM state_facts WHERE subject=? AND predicate=? '
        'AND valid_to IS NULL', (subject, predicate)).fetchone()


# ── the splitter itself ─────────────────────────────────────────────────────
# Every `value` below is a real one from minerva.db (2026-09-11), truncated.
LIVE_CASES = [
    ('status', 'DEAD confirmed 2026-08-13 — no power, will not POST.',
     'DEAD', 'state_anchored'),
    ('status', 'tailnet OFFLINE since 2026-07-26 (46d as of 2026-09-10) — AIO powered off',
     'OFFLINE', 'state_vocabulary'),
    ('status', 'PROVISIONING 2026-07-21 — Frank installing Xubuntu 26.04.',
     'PROVISIONING', 'state_anchored'),
    ('status', 'LIVE_2026-08-28_wq1039;off-fleet_CF-Worker_deadman.lumennode.uk_cron5m',
     'LIVE_2026-08-28_wq1039', 'state_anchored'),
    ('availability', 'IDLE + available — Xeon 2699 v2, more usable than khepri',
     'IDLE', 'state_anchored'),
    ('root_disk', '227G/466G 51% used, 220G free after 2026-08-09 offload (was 365G/82%)',
     '227G/466G', 'measure_size_pair'),
    ('root_disk', '80% used (357G/466G), 90G free. Growth traced to the 08-27 stack.',
     '357G/466G', 'measure_size_pair'),
    ('power_plug', 'P3 (relabelled 2026-08-11 after the strip swap)',
     'P3', 'prefix_token'),
]


@pytest.mark.parametrize('predicate,value,token,rule', LIVE_CASES)
def test_split_live_corpus_values(predicate, value, token, rule):
    got, note, got_rule = svs.split_value(predicate, value)
    assert (got, got_rule) == (token, rule)
    assert note, 'the prose has to land somewhere'


@pytest.mark.parametrize('predicate,value,token,rule', LIVE_CASES)
def test_emitted_tokens_are_scanner_structured(predicate, value, token, rule):
    """The whole point: what the splitter writes to `value` must be a value the
    contradiction scanner will index. `is_structured` is the scanner's own gate
    (lumen_contradiction_scan.py:127), so this is its opinion, not ours."""
    got, _note, _rule = svs.split_value(predicate, value)
    assert not lcs.is_structured(value, 'status'), 'fixture must start unreadable'
    assert lcs.is_structured(got, 'status')


def test_token_value_is_left_alone():
    assert svs.split_value('status', 'ONLINE') == ('ONLINE', None, None)
    assert svs.split_value('lan_ip', '100.117.66.102') == ('100.117.66.102', None, None)


def test_no_rule_means_no_split():
    """Prose with no token in it stays prose. Inventing a token would be the
    string-matching-on-prose that MINERVA_STALENESS_REVIEW.md §3.5 forbids."""
    prose = ("Thor is Frank's LAPTOP (Galaxy Book Pro 360), NOT always-on "
             "infrastructure. Offline is normal, not an outage.")
    value, note, rule = svs.split_value('availability', prose)
    assert (value, note, rule) == (prose, None, None)


def test_prefix_stopword_is_not_a_token():
    value, _note, rule = svs.split_value('status', 'Frank: still deciding on the swap')
    assert rule is None and value.startswith('Frank')


def test_explicit_note_is_authoritative():
    """A caller who splits it themselves gets their value stored verbatim."""
    assert svs.split_value('status', 'IN SERVICE', note='since the 09-02 reboot') == (
        'IN SERVICE', 'since the 09-02 reboot', 'explicit')


def test_leading_token_leaves_remainder_as_note():
    _v, note, _r = svs.split_value('status', 'DEAD — no power, will not POST')
    assert note == 'no power, will not POST'


def test_midsentence_token_keeps_the_whole_sentence_as_note():
    """A token cut out of the middle would mangle the sentence, so the note is
    the original value — one word redundant, nothing lost."""
    value = '80% used (357G/466G), 90G free'
    _v, note, _r = svs.split_value('root_disk', value)
    assert note == value


# ── ingest ──────────────────────────────────────────────────────────────────
def test_ingest_splits_prose_into_value_and_note(conn):
    res = ls.ingest('testnode', 'status',
                    'OFFLINE since 2026-07-26 — AIO powered off, no ssh path',
                    source_ref='wq#1629-test', conn=conn)
    assert res['value'] == 'OFFLINE'
    assert res['split'] == 'state_anchored'
    row = _row(conn, 'testnode', 'status')
    assert row['value'] == 'OFFLINE'
    assert row['note'] == 'since 2026-07-26 — AIO powered off, no ssh path'


def test_ingest_note_flag_stores_value_verbatim(conn):
    ls.ingest('testnode', 'status', 'OFFLINE', note='AIO powered off',
              source_ref='wq#1629-test', conn=conn)
    row = _row(conn, 'testnode', 'status')
    assert (row['value'], row['note']) == ('OFFLINE', 'AIO powered off')


def test_fresh_prose_for_the_same_token_is_a_reconfirm_not_a_supersession(conn):
    """Before the split, 'OFFLINE since 46d' and 'OFFLINE since 47d' were two
    different values, so re-observing an unchanged state opened a new row and
    closed the old one. Now only the prose moves."""
    ls.ingest('testnode', 'status', 'OFFLINE since 2026-07-26 (46d)',
              source_ref='wq#1629-test', conn=conn)
    res = ls.ingest('testnode', 'status', 'OFFLINE since 2026-07-26 (47d)',
                    source_ref='wq#1629-test', conn=conn)
    assert res['action'] == 'reconfirmed'
    rows = conn.execute('SELECT value, note FROM state_facts WHERE subject=?',
                        ('testnode',)).fetchall()
    assert len(rows) == 1
    assert rows[0]['note'] == 'since 2026-07-26 (47d)'


def test_bare_token_reingest_keeps_the_stored_note(conn):
    ls.ingest('testnode', 'status', 'OFFLINE — AIO powered off',
              source_ref='wq#1629-test', conn=conn)
    ls.ingest('testnode', 'status', 'OFFLINE', source_ref='wq#1629-probe', conn=conn)
    assert _row(conn, 'testnode', 'status')['note'] == 'AIO powered off'


def test_lifecycle_enum_is_never_split(conn):
    ls.ingest('testnode', 'lifecycle', 'retired', source_ref='wq#1629-test', conn=conn)
    row = _row(conn, 'testnode', 'lifecycle')
    assert (row['value'], row['note']) == ('retired', None)


def test_a_real_state_change_still_supersedes(conn):
    ls.ingest('testnode', 'status', 'OFFLINE since 2026-07-26',
              source_ref='wq#1629-test', conn=conn)
    res = ls.ingest('testnode', 'status', 'RESTORED 2026-09-10 — Frank re-plugged it',
                    source_ref='wq#1629-test', conn=conn)
    assert res['action'] == 'superseded'
    closed = conn.execute('SELECT value FROM state_facts WHERE valid_to IS NOT NULL').fetchone()
    assert closed['value'] == 'OFFLINE'


def test_current_renders_token_plus_note(conn):
    ls.ingest('testnode', 'status', 'OFFLINE — AIO powered off',
              source_ref='wq#1629-test', conn=conn)
    row = ls.current('testnode', 'status', conn=conn)[0]
    assert row['value'] == 'OFFLINE'
    assert row['value_display'] == 'OFFLINE — AIO powered off'


# ── backfill ────────────────────────────────────────────────────────────────
def _seed_prose(conn, subject, predicate, value, closed=False):
    """Write a prose value the way the store held it before wq#1629 — straight
    INSERT, bypassing ingest, since ingest now splits."""
    import uuid
    now = ls._now()
    conn.execute(
        'INSERT INTO state_facts (id, subject, predicate, value, valid_from, '
        'valid_to, ingested_at, fact_class, render_tier) VALUES (?,?,?,?,?,?,?,?,?)',
        (uuid.uuid4().hex, subject, predicate, value, '2026-07-01T00:00:00Z',
         now if closed else None, now, 'narrative', 'topic'))
    conn.commit()


def test_backfill_dry_run_changes_nothing(conn):
    _seed_prose(conn, 'oldnode', 'status', 'DEAD — no power since 2026-08-13')
    changes, _skipped = svs.plan(conn)
    assert [c['value'] for c in changes] == ['DEAD']
    assert _row(conn, 'oldnode', 'status')['value'].startswith('DEAD —')


def test_backfill_apply_rewrites_value_and_note(conn):
    _seed_prose(conn, 'oldnode', 'status', 'DEAD — no power since 2026-08-13')
    changes, _ = svs.plan(conn)
    svs.apply(conn, changes, source_ref='wq#1629-test')
    row = _row(conn, 'oldnode', 'status')
    assert (row['value'], row['note']) == ('DEAD', 'no power since 2026-08-13')


def test_backfill_preserves_the_bitemporal_window(conn):
    """A split is the same fact re-shaped. Touching valid_from/valid_to would
    rewrite history — `asof` queries must answer identically after a backfill."""
    _seed_prose(conn, 'oldnode', 'status', 'DEAD — no power', closed=True)
    before = conn.execute('SELECT id, valid_from, valid_to, ingested_at '
                          'FROM state_facts').fetchone()
    changes, _ = svs.plan(conn)
    svs.apply(conn, changes, source_ref='wq#1629-test')
    after = conn.execute('SELECT id, valid_from, valid_to, ingested_at '
                         'FROM state_facts').fetchone()
    assert tuple(after) == tuple(before)


def test_backfill_recomputes_fact_class(conn):
    """fact_class=narrative is its own scanner skip (measure_reach.py), and it
    was assigned from the length of the prose. The token is not narrative."""
    _seed_prose(conn, 'oldnode', 'status', 'DEAD — no power since 2026-08-13')
    changes, _ = svs.plan(conn)
    svs.apply(conn, changes, source_ref='wq#1629-test')
    row = _row(conn, 'oldnode', 'status')
    assert row['fact_class'] == ls._classify_fact('status', 'DEAD')[0] != 'narrative'


def test_split_does_not_promote_a_fact_into_the_core_block(conn):
    """render_tier decides what goes into the injected LIVE_FLEET_STATE block,
    which has a token budget (lumen_state_gen.STATE_SOFT_CAP_TOK). A split
    renders the same characters as before, so it must not change the tier —
    measured live at 5375 -> 8703 tokens against a 4000 cap when the backfill
    recomputed the tier from the token alone."""
    prose = ('FULL-FLEET 2026-07-12: AB_RAG_RETRIEVAL=1 on all 4 fleet workers '
             '(cerebras/mistral/sambanova via 30-abrag.conf, openrouter via '
             '30-abrag-canary.conf). Fail-open local_retrieval node in '
             'lumen_fleet_graph.py. Rollback: rm drop-ins + daemon-reload + '
             'restart. Verified live on all four workers the same evening.')
    _seed_prose(conn, 'oldnode', 'status', prose)
    assert ls._classify_fact('status', prose)[1] == 'topic'
    changes, _ = svs.plan(conn)
    svs.apply(conn, changes, source_ref='wq#1629-test')
    assert _row(conn, 'oldnode', 'status')['render_tier'] == 'topic'


def test_ingest_tier_measures_value_plus_note(conn):
    ls.ingest('testnode', 'status', 'OFFLINE', note='x' * 300,
              source_ref='wq#1629-test', conn=conn)
    assert _row(conn, 'testnode', 'status')['render_tier'] == 'topic'


def test_backfill_audits_every_row_it_rewrites(conn):
    _seed_prose(conn, 'oldnode', 'status', 'DEAD — no power since 2026-08-13')
    changes, _ = svs.plan(conn)
    svs.apply(conn, changes, source_ref='wq#1629-test')
    audit = conn.execute("SELECT action, source_ref, detail FROM memory_audit "
                         "WHERE table_name='state_facts'").fetchall()
    assert len(audit) == 1
    assert audit[0]['source_ref'] == 'wq#1629-test'
    assert 'wq#1629 value/note split' in audit[0]['detail']


def test_backfill_is_idempotent(conn):
    _seed_prose(conn, 'oldnode', 'status', 'DEAD — no power since 2026-08-13')
    changes, _ = svs.plan(conn)
    svs.apply(conn, changes, source_ref='wq#1629-test')
    again, _ = svs.plan(conn)
    assert again == []


def test_backfill_skips_unsplittable_prose(conn):
    prose = 'Ma’at vault HA standby, promoted by hand only — see the runbook'
    _seed_prose(conn, 'oldnode', 'role', prose)
    changes, skipped = svs.plan(conn, predicates=None)
    assert changes == []
    assert [s['value'] for s in skipped] == [prose]


def test_backfill_makes_a_dead_value_scannable(conn):
    """End to end, the thing the ticket measures: a closed fact the scanner
    could not read before the backfill, indexed after it."""
    _seed_prose(conn, 'oldnode', 'status', 'DEAD — no power since 2026-08-13',
                closed=True)
    before = conn.execute('SELECT value, fact_class FROM state_facts').fetchone()
    assert not lcs.is_structured(before['value'], before['fact_class'])
    changes, _ = svs.plan(conn)
    svs.apply(conn, changes, source_ref='wq#1629-test')
    after = conn.execute('SELECT value, fact_class FROM state_facts').fetchone()
    assert lcs.is_structured(after['value'], after['fact_class'])
