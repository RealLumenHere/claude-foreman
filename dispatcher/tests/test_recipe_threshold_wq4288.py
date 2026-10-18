"""wq#4288: freeze threshold 5 (spec R2), the flock'd reconcile of freezes made under
the old default of 3, and concurrent record_outcome() on the JSON store."""
import json
import multiprocessing
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import recipe_crystallizer as rc  # noqa: E402

# Byte copy of the live /home/lumen/foreman/dispatcher/recipes.json as fleet-ops2 found
# it 2026-10-18: config-edit frozen on claude-sonnet-5::standard-engineering at streak 3.
LIVE_FREEZE = os.path.join(HERE, "fixtures", "recipes_wq4288_live_freeze.json")
CELL = ("claude-sonnet-5", "standard-engineering")


@pytest.fixture(autouse=True)
def _scratch_store(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "STORE_PATH", str(tmp_path / "recipes.json"))
    monkeypatch.setattr(rc, "FREEZE_THRESHOLD", 5)


def test_default_threshold_is_five():
    env = {k: v for k, v in os.environ.items() if k != "FOREMAN_RECIPE_FREEZE_THRESHOLD"}
    out = subprocess.run(
        [sys.executable, "-c", "import recipe_crystallizer as r; print(r.FREEZE_THRESHOLD)"],
        cwd=os.path.dirname(HERE), env=env, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "5"


def test_four_wins_do_not_freeze_the_fifth_does():
    for n in range(1, 5):
        result = rc.record_outcome("test-fix", *CELL, True)
        assert result["streak"] == n and "frozen_at" not in result
        assert not rc.is_frozen("test-fix")
    result = rc.record_outcome("test-fix", *CELL, True)
    assert result["streak_at_freeze"] == 5
    assert rc.lookup_recipe("test-fix")["model"] == CELL[0]


def test_reconcile_releases_the_live_streak_3_freeze_and_keeps_the_streak():
    shutil.copy(LIVE_FREEZE, rc.STORE_PATH)
    assert rc.is_frozen("config-edit"), "precondition: fixture carries the live freeze"
    released = rc.reconcile_freezes()
    assert released["config-edit"]["streak_at_freeze"] == 3
    assert not rc.is_frozen("config-edit")
    store = rc.load_store()
    cell = store["cells"]["config-edit"]["::".join(CELL)]
    assert (cell["streak"], cell["successes"], cell["failures"]) == (3, 3, 0)
    # the sibling cell is untouched
    assert store["cells"]["config-edit"]["claude-sonnet-5::mechanical-edit"]["streak"] == 1
    # streak intact => two more wins re-freeze it at exactly 5, not 3+5
    assert "frozen_at" not in rc.record_outcome("config-edit", *CELL, True)
    result = rc.record_outcome("config-edit", *CELL, True)
    assert result["streak_at_freeze"] == 5
    assert rc.is_frozen("config-edit")


def test_reconcile_keeps_a_freeze_that_meets_the_threshold():
    for _ in range(5):
        rc.record_outcome("code-refactor", *CELL, True)
    assert rc.reconcile_freezes() == {}
    assert rc.is_frozen("code-refactor")


def test_reconcile_dry_run_writes_nothing():
    shutil.copy(LIVE_FREEZE, rc.STORE_PATH)
    before = open(rc.STORE_PATH).read()
    assert "config-edit" in rc.reconcile_freezes(dry_run=True)
    assert open(rc.STORE_PATH).read() == before


def test_reconcile_is_idempotent():
    shutil.copy(LIVE_FREEZE, rc.STORE_PATH)
    assert "config-edit" in rc.reconcile_freezes()
    assert rc.reconcile_freezes() == {}


def test_reconcile_cli_against_the_store_env(tmp_path):
    store = tmp_path / "cli.json"
    shutil.copy(LIVE_FREEZE, store)
    env = dict(os.environ, FOREMAN_RECIPE_STORE=str(store))
    env.pop("FOREMAN_RECIPE_FREEZE_THRESHOLD", None)
    out = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE),
                                                       "recipe_crystallizer.py"), "reconcile"],
                         env=env, capture_output=True, text=True, check=True)
    report = json.loads(out.stdout)
    assert report["threshold"] == 5 and list(report["released"]) == ["config-edit"]
    assert json.loads(store.read_text())["frozen"] == {}


def _hammer(store_path, n):
    rc.STORE_PATH = store_path
    for _ in range(n):
        rc.record_outcome("test-fix", *CELL, True)


def test_concurrent_record_outcome_loses_no_updates():
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_hammer, args=(rc.STORE_PATH, 25)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    assert all(p.exitcode == 0 for p in procs)
    cell = rc.load_store()["cells"]["test-fix"]["::".join(CELL)]
    assert cell["successes"] == 100
    # froze once, at 5, and frozen-cell wins stop advancing the streak
    assert cell["streak"] == 5
    assert rc.lookup_recipe("test-fix")["streak_at_freeze"] == 5
