"""wq#4553 (wq#4539 phase 1): context_builder roster/episodes read minerva.db
live, not frozen PG. Probe (a)+(b) from design/wq4539_one_render_one_truth.md:
(a) roster is the live state-fact node set, byte-stable across two runs;
(b) a retired node present in PG fleet_devices must NOT render, even when
minerva.db still carries a (now-retired) `.role` fact for it.

`fixtures/state_sample_2026-10-23.txt` is a pinned, immutable snapshot of real
`lumen_state.py current` output captured 2026-10-23 (thor: active; khepri:
RETIRED via both role and status text; sekhmet: down but not retired) — not a
live mutable file, so this test is re-runnable evidence (ISSUE-LOG doctrine).
"""
import importlib.util
import os
import sqlite3
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")


def _load():
    spec = importlib.util.spec_from_file_location(
        "cb_wq4553", os.path.join(HERE, "context_builder.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fm_db"] = types.SimpleNamespace(connect=lambda: None)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cb():
    return _load()


@pytest.fixture
def state_text():
    with open(os.path.join(FIXTURES, "state_sample_2026-10-23.txt"), encoding="utf-8") as fh:
        return fh.read()


def test_parse_state_lines_handles_confidence_tag(cb, state_text):
    """Regression: fleet_context.py's end-anchored pattern
    (`\\(since DATE\\)\\s*$`) matches 0 of these lines because every live line
    carries a trailing confidence tag after the date — verified against the
    live fleet 2026-10-23 (1244/1244 current lines tagged). This parser must
    not repeat that anchor."""
    facts = cb.parse_state_lines(state_text)
    assert len(facts) == 12
    thor_role = next(f for f in facts if f["subject"] == "thor" and f["predicate"] == "role")
    assert thor_role["value"] == "fleet workstation / TTS"
    assert thor_role["since"] == "2026-06-18"


def test_parse_state_lines_binds_last_since_clause(cb):
    """wq#4578 regression (wq#4572's contradiction): the value group is greedy,
    so a value whose own free text contains a "(since DATE)" fragment binds to
    the LAST clause — the one lumen_state.py appends — not the first. The
    non-greedy `.*?` this replaces parsed line 2 of the fixture as
    value="flaky", since="2020-01-01": truncated, six years stale, no error."""
    with open(os.path.join(FIXTURES, "state_embedded_since_wq4578.txt"),
              encoding="utf-8") as fh:
        facts = cb.parse_state_lines(fh.read())
    got = {(f["subject"], f["predicate"]): (f["value"], f["since"]) for f in facts}
    assert len(facts) == 6
    assert got[("testhost", "status")] == (
        "flaky (since 2020-01-01) but recovered", "2026-10-11")
    assert got[("testhost", "role")] == (
        "spare builder, took over ra's lane (retired since 2026-03-02)", "2026-09-14")
    assert got[("dualhost", "lan_ip")] == (
        "192.168.8.44 (static lease since 2025-12-01; see wq#4539)", "2026-08-14")
    # No tag after the date at all: the dropped end anchor still must not matter.
    assert got[("tailhost", "role")] == ("watchdog (since 2024-05-05)", "2026-10-22")
    # Empty value stays an empty string, never None (the `|` branch).
    assert got[("emptyhost", "status")] == ("", "2026-10-20")


def test_parse_state_lines_greedy_value_keeps_live_coverage(cb, state_text):
    """The greedy value group must not change WHICH lines match: same 12 facts
    off the pinned live snapshot as before wq#4578, and a retired-role value
    still reads as retired (greedy must not swallow the leading word)."""
    facts = cb.parse_state_lines(state_text)
    assert len(facts) == 12
    khepri_role = next(f for f in facts
                       if f["subject"] == "khepri" and f["predicate"] == "role")
    assert cb._is_retired(khepri_role["value"])


def test_roster_excludes_retired_node_present_in_minerva(cb, state_text):
    """Probe (b): khepri has a CURRENT `.role` fact (minerva.db never deletes
    history) but both role and status read retired — must not render."""
    hosts = [h for h, _ in cb.roster(state_text)]
    assert "khepri" not in hosts
    assert "thor" in hosts
    assert "sekhmet" in hosts  # down, not retired — stays


def test_roster_excludes_node_absent_from_state_facts(cb):
    """Probe (b), the simpler case: a PG-only host with no state fact at all
    (e.g. a decommissioned node PG never learned about) must not render."""
    hosts = [h for h, _ in cb.roster("")]
    assert hosts == []


def test_roster_sorted_and_byte_stable(cb, state_text):
    """Probe (a): two builds from the same state snapshot are identical."""
    first = cb.roster(state_text)
    second = cb.roster(state_text)
    assert first == second
    assert [h for h, _ in first] == sorted(h for h, _ in first)


def test_roster_renders_without_retired_host(cb, state_text):
    class _Cur:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return []

        def close(self):
            pass

    class _Conn:
        def cursor(self):
            return _Cur()

    cb._run_state_current = lambda: state_text
    text = cb.build(_Conn())
    assert "khepri" not in text
    assert "thor" in text
    assert "## FLEET DEVICES" in text


def _episodes_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE archival_notes (
            id TEXT PRIMARY KEY, kind TEXT, title TEXT, content TEXT,
            source_ref TEXT, created_at TEXT, revoked_at TEXT, superseded_by TEXT);
        CREATE TABLE note_merges (absorbed_id TEXT);
    """)
    rows = [
        ("e1", "code-ship", "wq#1 attempt 1 SUCCESS", "wq#1", "2026-10-23T09:00:00", None, None),
        ("e2", "session", "wq#2 attempt 1 LIMIT", "wq#2", "2026-10-22T09:00:00", None, None),
        ("e3", "code-ship", "wq#3 revoked", "wq#3", "2026-10-21T09:00:00", "2026-10-22T00:00:00", None),
        ("e4", "code-ship", "wq#4 superseded", "wq#4", "2026-10-20T09:00:00", None, "e1"),
        ("e5", "code-ship", "wq#5 merged away", "wq#5", "2026-10-19T09:00:00", None, None),
        ("e6", "infra", "wq#6 wrong kind", "wq#6", "2026-10-18T09:00:00", None, None),
    ]
    con.executemany(
        "INSERT INTO archival_notes VALUES (?,?,?,?,?,?,?,?)",
        [(r[0], r[1], r[2], "x", r[3], r[4], r[5], r[6]) for r in rows])
    con.execute("INSERT INTO note_merges VALUES ('e5')")
    con.commit()
    con.close()


def test_episodes_filters_revoked_superseded_merged_and_kind(cb, tmp_path):
    db = tmp_path / "minerva.db"
    _episodes_db(str(db))
    rows = cb.episodes(db_path=str(db))
    titles = [r[0] for r in rows]
    assert titles == ["wq#1 attempt 1 SUCCESS", "wq#2 attempt 1 LIMIT"]


def test_episodes_missing_db_fails_open(cb, tmp_path):
    assert cb.episodes(db_path=str(tmp_path / "missing.db")) == []
