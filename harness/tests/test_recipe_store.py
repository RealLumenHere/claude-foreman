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
