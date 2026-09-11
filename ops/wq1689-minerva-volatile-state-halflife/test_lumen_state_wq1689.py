"""wq#1689 tests — volatile-predicate half-life at ingest + near-duplicate
predicate lint, run against the real `/home/lumen/minerva/lumen_state.py`
(not a copy) with `lumen_state.DB` pointed at a throwaway temp file, so these
exercise the actual production module without touching minerva.db.

Run: python3 -m pytest -q ops/wq1689-minerva-volatile-state-halflife/test_lumen_state_wq1689.py
(needs /home/lumen/minerva on sys.path, done below via conftest-free sys.path
insert since minerva/** is not a git repo -- see README.md "Why a diff, not
committed source").
"""
import os
import sys
import tempfile
import sqlite3

import pytest

sys.path.insert(0, '/home/lumen/minerva')
import lumen_state as ls  # noqa: E402


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


def test_volatile_predicate_gets_default_half_life(conn):
    """status/kernel/availability/root_disk/uptime/ssh_status are one-moment
    probe observations (INVENTORY_RECONCILIATION.md #6/#12, wq#1666) -- ingest
    must stamp half_life_days for them even when the caller passes none."""
    r = ls.ingest('testnode', 'status', 'ONLINE', source_ref='wq#1689-test', conn=conn)
    assert r['action'] == 'insert'
    row = conn.execute(
        'SELECT half_life_days FROM state_facts WHERE subject=? AND predicate=?',
        ('testnode', 'status')).fetchone()
    assert row['half_life_days'] == ls.VOLATILE_HALFLIFE['status'] == 7


def test_stable_predicate_stays_null_half_life(conn):
    """Identity facts (hardware, mac, ...) are not in VOLATILE_HALFLIFE and
    must stay NULL -> never decay."""
    ls.ingest('testnode', 'hardware', 'Trigkey Green G4', source_ref='wq#1689-test', conn=conn)
    row = conn.execute(
        'SELECT half_life_days FROM state_facts WHERE subject=? AND predicate=?',
        ('testnode', 'hardware')).fetchone()
    assert row['half_life_days'] is None


def test_registry_override_wins_over_module_default(conn):
    """volatile_halflife_days: a state_registry.json 'volatile_halflife' entry
    overrides the VOLATILE_HALFLIFE module default (registry wins on conflict)."""
    reg = ls.registry()
    try:
        reg['volatile_halflife']['status'] = 3
        assert ls.volatile_halflife_days('status', reg) == 3
    finally:
        reg['volatile_halflife'].pop('status', None)


def test_volatile_half_life_ages_out_to_due_in_current(conn):
    """The half-life this ticket adds feeds effective_confidence()'s decay,
    which staleness()/current() already read (wq#1612) -- so a volatile fact
    whose evidence anchor (last_confirmed_at) is stale renders DUE/STALE and
    its eff_confidence decays, instead of staying present-tense forever the
    way thoth.status did for 8 days after the 09-02 outage resolved (wq#1666).
    ssh_status (not lan_ip/status/etc, which IDENTITY_PREDS classifies as
    fact_class=identity with a 90d verify interval) matches the `status`
    fact_class (3d interval) so a 10-day-stale anchor crosses STALE_RATIO."""
    from datetime import datetime, timedelta, timezone
    ls.ingest('testnode', 'ssh_status', 'up', source_ref='wq#1689-test', conn=conn)
    stale_anchor = (datetime(2026, 9, 11, tzinfo=timezone.utc) - timedelta(days=10)).isoformat()
    conn.execute(
        "UPDATE state_facts SET valid_from=?, last_confirmed_at=? "
        "WHERE subject='testnode' AND predicate='ssh_status'",
        (stale_anchor, stale_anchor))
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    rows = ls.current('testnode', 'ssh_status', now=now, conn=conn)
    assert rows[0]['staleness'] == ls.STALE
    assert rows[0]['eff_confidence'] < 0.5


def test_near_duplicate_predicate_refused(conn):
    """The supersession hole this ticket closes: a renamed/reformatted
    predicate within edit-distance 2 of one the subject already carries must
    be refused, not silently opened as a second never-invalidated fact."""
    ls.ingest('testnode', 'uptime', '5d', source_ref='wq#1689-test',
              new_predicate='volatile probe', conn=conn)
    with pytest.raises(ls.PredicateNearDuplicate):
        ls.ingest('testnode', 'uptme', '5d', source_ref='wq#1689-test',
                  new_predicate='typo', conn=conn)


def test_far_predicate_not_flagged_as_duplicate(conn):
    """A genuinely new axis (edit-distance > 2 from anything the subject
    already carries) must still be accepted with a reason -- the lint is
    scoped to near-misses, not a blanket new-predicate block."""
    ls.ingest('testnode', 'uptime', '5d', source_ref='wq#1689-test',
              new_predicate='volatile probe', conn=conn)
    r = ls.ingest('testnode', 'grafana_version', '13.1.1', source_ref='wq#1689-test',
                  new_predicate='unrelated axis', conn=conn)
    assert r['action'] == 'insert'


def test_edit_distance_examples_from_ticket():
    """Sanity check on the primitive itself against the ticket's own
    formatting-typo example (lan_ip vs lan-ip)."""
    assert ls._edit_distance('lan_ip', 'lan-ip') == 1
    assert ls._edit_distance('lan_ip', 'lan_ip') == 0


def test_hygiene_pass_aliases_resolve_against_real_registry():
    """The one-time hygiene pass this ticket calls for (ts_ip->tailscale_ip,
    lan-ip->lan_ip, hw->hardware, role_status->status) is config, not code --
    verify it's actually wired into the live `state_registry.json` that
    `canonical_predicate()` reads, not just asserted in the README's prose."""
    reg = ls.registry()
    assert ls.canonical_predicate('ts_ip', reg) == 'tailscale_ip'
    assert ls.canonical_predicate('lan-ip', reg) == 'lan_ip'
    assert ls.canonical_predicate('hw', reg) == 'hardware'
    assert ls.canonical_predicate('role_status', reg) == 'status'
