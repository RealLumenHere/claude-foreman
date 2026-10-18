"""wq#4288: harness/recipe_store.py — fleet_memory PG backend for crystallized recipes.

The fail-open tests always run. The PG tests run against the real fleet_memory only when
FOREMAN_RECIPE_PG_TEST=1, each in its own scratch table (recipes_test_<hex>, dropped at
teardown), so a plain `pytest` never writes to the production database."""
import json
import multiprocessing
import os
import shutil
import sys
import uuid

import pytest

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS)
import recipe_store as rs  # noqa: E402
import recipe_crystallizer as rc  # noqa: E402

LIVE_FREEZE = os.path.join(os.path.dirname(HARNESS), "dispatcher", "tests", "fixtures",
                           "recipes_wq4288_live_freeze.json")
CELL = ("claude-sonnet-5", "standard-engineering")


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "STORE_PATH", str(tmp_path / "recipes.json"))
    monkeypatch.setattr(rc, "FREEZE_THRESHOLD", 5)
    monkeypatch.setattr(rs, "_pg_down_until", 0.0)


@pytest.fixture
def pg_down(monkeypatch, tmp_path):
    """PG 'on' but nothing listening: a closed local port and throwaway credentials."""
    env = tmp_path / "fake.env"
    env.write_text("DATABASE_URL=postgresql://nobody:x@db/none\n")
    monkeypatch.setenv("FOREMAN_RECIPE_PG", "on")
    monkeypatch.setattr(rs, "ENV_FILE", str(env))
    monkeypatch.setattr(rs, "PG_PORT", 1)


def test_pg_down_reads_fail_open_to_nothing_frozen(pg_down):
    shutil.copy(LIVE_FREEZE, rc.STORE_PATH)  # JSON has a freeze; PG-down must not surface it
    assert rs.lookup_recipe("config-edit") is None
    assert rs.is_frozen("config-edit") is False
    assert rs.load_store() == {"cells": {}, "frozen": {}}
    assert rs.success_rates() == []


def test_pg_down_record_outcome_falls_back_to_json(pg_down):
    result = rs.record_outcome("test-fix", *CELL, True)
    assert result["streak"] == 1
    assert rc.load_store()["cells"]["test-fix"]["::".join(CELL)]["successes"] == 1


def test_pg_down_trips_the_retry_window(pg_down, monkeypatch):
    rs.lookup_recipe("test-fix")
    assert rs._pg_down_until > 0
    calls = []
    monkeypatch.setattr(rs, "_db_credentials", lambda: calls.append(1) or ("u", "p"))
    rs.lookup_recipe("test-fix")
    assert calls == [], "inside the retry window no connect is attempted"


def test_missing_driver_or_env_file_fails_open(monkeypatch, tmp_path):
    monkeypatch.setenv("FOREMAN_RECIPE_PG", "on")
    monkeypatch.setattr(rs, "ENV_FILE", str(tmp_path / "absent.env"))
    assert rs.lookup_recipe("test-fix") is None
    assert rs.record_outcome("test-fix", *CELL, False)["failures"] == 1


def test_pg_off_routes_writes_to_json_and_reads_nothing():
    assert os.environ["FOREMAN_RECIPE_PG"] == "off"  # root conftest
    rs.record_outcome("test-fix", *CELL, True)
    assert rc.load_store()["cells"]["test-fix"]
    assert rs.lookup_recipe("test-fix") is None


def test_untracked_type_is_a_noop():
    assert rs.record_outcome(None, *CELL, True) is None
    assert rs.record_outcome("rca-investigation", *CELL, True) is None
    assert rs.record_outcome("test-fix", "", "x", True) is None


def test_bad_table_name_fails_open(monkeypatch):
    monkeypatch.setenv("FOREMAN_RECIPE_PG", "on")
    monkeypatch.setenv("FOREMAN_RECIPE_TABLE", "recipes; DROP TABLE fleet_facts")
    assert rs.lookup_recipe("test-fix") is None


# ---- wq#4310: the JSON-fallback journal ---------------------------------------------

def _journal_lines():
    with open(rs.journal_path(), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_pg_down_journals_what_pg_owes(pg_down):
    rs.record_outcome("test-fix", *CELL, True)
    rs._pg_down_until = 0.0
    rs.record_outcome("test-fix", *CELL, False)
    owed = _journal_lines()
    assert [(e["ticket_type"], e["model"], e["success"]) for e in owed] == [
        ("test-fix", CELL[0], True), ("test-fix", CELL[0], False)]
    assert rs.journal_path().startswith(rc.STORE_PATH), "journal follows the JSON store"


def test_pg_off_is_a_backend_choice_not_an_outage_so_never_journals():
    assert os.environ["FOREMAN_RECIPE_PG"] == "off"  # root conftest
    rs.record_outcome("test-fix", *CELL, True)
    assert rc.load_store()["cells"]["test-fix"], "the outcome still reached JSON"
    assert not os.path.exists(rs.journal_path()), "PG is not owed an outcome it opted out of"


def test_drain_with_pg_still_down_keeps_every_outcome_pending(pg_down):
    rs.record_outcome("code-refactor", *CELL, True)
    rs._pg_down_until = 0.0
    assert rs.drain_journal() == {"replayed": 0, "pending": 1, "dropped": 0}
    assert len(_journal_lines()) == 1, "nothing is dropped until PG has taken it"


def test_drain_of_an_absent_or_empty_journal_is_a_noop():
    empty = {"replayed": 0, "pending": 0, "dropped": 0}
    assert rs.drain_journal() == empty
    with open(rs.journal_path(), "w", encoding="utf-8"):
        pass
    assert rs.drain_journal() == empty


def test_drain_drops_unreplayable_lines(pg_down):
    rs.record_outcome("test-fix", *CELL, True)
    with open(rs.journal_path(), "a", encoding="utf-8") as fh:
        fh.write("{truncated\n")
        fh.write(json.dumps({"ticket_type": "rca-investigation", "model": "m",
                             "prompt_template": "t", "success": True}) + "\n")
    rs._pg_down_until = 0.0
    assert rs.drain_journal() == {"replayed": 0, "pending": 1, "dropped": 2}
    assert len(_journal_lines()) == 1, "corruption is dropped, the real outcome is kept"


# ---- real PostgreSQL ----------------------------------------------------------------

needs_pg = pytest.mark.skipif(os.environ.get("FOREMAN_RECIPE_PG_TEST") != "1",
                              reason="set FOREMAN_RECIPE_PG_TEST=1 to run against fleet_memory")


@pytest.fixture
def pg_table(monkeypatch):
    table = f"recipes_test_{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("FOREMAN_RECIPE_PG", "on")
    monkeypatch.setenv("FOREMAN_RECIPE_TABLE", table)
    conn = rs._connect()
    with conn, conn.cursor() as cur:
        for stmt in rs._ddl():
            cur.execute(stmt)
    yield table
    with conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table}")
    conn.close()


@needs_pg
def test_pg_threshold_five_boundary(pg_table):
    for n in range(1, 5):
        assert rs.record_outcome("test-fix", *CELL, True)["streak"] == n
        assert rs.lookup_recipe("test-fix") is None
    assert rs.record_outcome("test-fix", *CELL, True)["streak_at_freeze"] == 5
    frozen = rs.lookup_recipe("test-fix")
    assert (frozen["model"], frozen["prompt_template"]) == CELL and frozen["frozen_at"]
    assert not rc.load_store()["cells"], "PG path must not touch the JSON fallback"


@needs_pg
def test_pg_single_loss_unfreezes_and_restarts_the_streak(pg_table):
    for _ in range(5):
        rs.record_outcome("code-refactor", *CELL, True)
    rs.record_outcome("code-refactor", *CELL, True)  # frozen win: wins++, streak holds
    result = rs.record_outcome("code-refactor", *CELL, False)
    assert (result["streak"], result["successes"], result["failures"]) == (0, 6, 1)
    assert rs.lookup_recipe("code-refactor") is None
    rows = rs._query(f"SELECT frozen, frozen_at, last_outcome FROM {pg_table}")
    assert rows == [(False, None, "loss")]


@needs_pg
def test_pg_one_frozen_cell_per_ticket_type(pg_table):
    other = ("claude-opus-5-5", "standard-engineering")
    for _ in range(5):
        rs.record_outcome("test-fix", *CELL, True)
    for _ in range(6):
        rs.record_outcome("test-fix", *other, True)
    assert rs.lookup_recipe("test-fix")["model"] == CELL[0]
    assert len(rs.load_store()["frozen"]) == 1


def _pg_hammer(ticket_type, model, n):
    rs._pg_down_until = 0.0
    for i in range(n):
        assert "model" in rs.record_outcome(ticket_type, model, "standard-engineering",
                                            i % 4 != 3)


@needs_pg
def test_pg_concurrent_record_outcome(pg_table):
    ctx = multiprocessing.get_context("fork")
    models = ["m-a", "m-a", "m-b", "m-b"]
    procs = [ctx.Process(target=_pg_hammer, args=("test-fix", m, 20)) for m in models]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
    assert all(p.exitcode == 0 for p in procs)
    rates = {r["model"]: r for r in rs.success_rates("test-fix")}
    for m in ("m-a", "m-b"):
        assert (rates[m]["wins"], rates[m]["losses"]) == (30, 10)
        assert rates[m]["success_rate"] == 0.75
    assert len(rs.load_store()["frozen"]) <= 1
    assert not rc.load_store()["cells"], "no write fell back to JSON"


@needs_pg
def test_pg_migration_of_the_live_freeze(pg_table):
    store = json.load(open(LIVE_FREEZE))
    conn = rs._connect()
    with conn, conn.cursor() as cur:
        assert rs.seed_from_json(cur, store, 5) == 2
        assert rs.seed_from_json(cur, store, 5) == 0, "re-seed never double-counts"
    conn.close()
    assert rs.lookup_recipe("config-edit") is None, "streak-3 freeze must not carry over"
    cell = rs.load_store()["cells"]["config-edit"]["::".join(CELL)]
    assert (cell["streak"], cell["successes"]) == (3, 3)
    rs.record_outcome("config-edit", *CELL, True)
    assert rs.lookup_recipe("config-edit") is None
    assert rs.record_outcome("config-edit", *CELL, True)["streak_at_freeze"] == 5


def _outage_outcomes(*successes):
    """Record outcomes while PG is unreachable, leaving them in the journal — a real
    fallback write, port 1, not a hand-built journal file."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rs, "PG_PORT", 1)
        for success in successes:
            rs._pg_down_until = 0.0
            rs.record_outcome("test-fix", *CELL, success)
    rs._pg_down_until = 0.0


@needs_pg
def test_pg_drains_outage_outcomes_so_they_still_count_toward_the_freeze(pg_table):
    _outage_outcomes(True, True, True, True)
    assert len(_journal_lines()) == 4
    assert rs._query(f"SELECT 1 FROM {pg_table}") == [], "PG missed all four"
    assert rs.record_outcome("test-fix", *CELL, True)["streak_at_freeze"] == 5
    assert rs.lookup_recipe("test-fix")["model"] == CELL[0]
    assert not os.path.exists(rs.journal_path()) or not _journal_lines()
    assert rs.drain_journal() == {"replayed": 0, "pending": 0, "dropped": 0}
    assert rs.success_rates("test-fix")[0]["wins"] == 5, "a re-drain never double-counts"


@needs_pg
def test_pg_drain_replays_in_recorded_order_not_as_an_aggregate(pg_table):
    _outage_outcomes(True, True, False, True)
    result = rs.record_outcome("test-fix", *CELL, True)
    assert (result["streak"], result["successes"], result["failures"]) == (2, 4, 1)
    assert rs.lookup_recipe("test-fix") is None, "the journaled loss broke the streak"


@needs_pg
def test_pg_drain_cap_leaves_the_rest_for_the_next_drain(pg_table):
    _outage_outcomes(True, True, True)
    assert rs.drain_journal(max_entries=2) == {"replayed": 2, "pending": 1, "dropped": 0}
    assert rs.load_store()["cells"]["test-fix"]["::".join(CELL)]["streak"] == 2
    assert rs.drain_journal(max_entries=2) == {"replayed": 1, "pending": 0, "dropped": 0}
    assert rs.load_store()["cells"]["test-fix"]["::".join(CELL)]["streak"] == 3


@needs_pg
def test_pg_seed_missing_repairs_a_cell_orphaned_before_the_journal(pg_table):
    """The wq#4304 live case: JSON holds a cell PG has never seen and no journal entry
    owes it, because the outage predates the journal. `drain --seed-missing` is the
    repair; it must leave a cell PG already knows untouched."""
    rs.record_outcome("config-edit", *CELL, True)  # PG knows this one: wins 1
    orphan = ("claude-opus-5-5", "rca-investigation")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rs, "PG_PORT", 1)
        rs.record_outcome("config-edit", *orphan, True)
        rs.record_outcome("config-edit", *CELL, True)  # JSON aggregate now disagrees
    os.remove(rs.journal_path())  # pre-journal orphan: nothing owes PG a replay
    rs._pg_down_until = 0.0
    assert rs.main(["drain", "--seed-missing"]) == 0
    rates = {r["model"]: r["wins"] for r in rs.success_rates("config-edit")}
    assert rates == {CELL[0]: 1, orphan[0]: 1}, "orphan seeded, known cell not overwritten"


@needs_pg
def test_pg_reconcile_releases_a_sub_threshold_freeze(pg_table):
    for _ in range(5):
        rs.record_outcome("test-fix", *CELL, True)
    conn = rs._connect()
    with conn, conn.cursor() as cur:
        assert rs.reconcile_pg(cur, 5) == []
        assert rs.reconcile_pg(cur, 6) == [["test-fix", *CELL]]
    conn.close()
    assert rs.lookup_recipe("test-fix") is None
    assert rs.load_store()["cells"]["test-fix"]["::".join(CELL)]["streak"] == 5
