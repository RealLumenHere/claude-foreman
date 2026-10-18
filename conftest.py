import importlib.util
import os
import sys

import pytest

# wq#3222: a repo-root `pytest` collects every test_*.py in the tree. The macbench agentic
# suite's repo/ and solution/ dirs are task templates that mb_agentic.py copies into a
# scratch git repo and scores there; their tests are meant to fail until a model under
# benchmark fixes them, and they import modules that only resolve inside that scratch repo.
collect_ignore_glob = ["tools/macbench/agentic/suite/*/repo", "tools/macbench/agentic/suite/*/solution"]

# wq#3222: the no-live-aq/ntfy guard covers every dir a root run collects, not only
# harness/tests. Loaded by file under its own name, so no dir goes onto sys.path, and
# harness/tests/conftest.py's `import live_bin_guard` gets this same module.
if "live_bin_guard" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "live_bin_guard", os.path.join(os.path.dirname(__file__), "harness", "tests", "live_bin_guard.py"))
    sys.modules["live_bin_guard"] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules["live_bin_guard"])
sys.modules["live_bin_guard"].install()


def pytest_sessionfinish(session, exitstatus):
    sys.modules["live_bin_guard"].session_finish(session)


# wq#3834: a credential-shaped str param id is printed masked, for every dir a root run
# collects. Loaded by file like live_bin_guard; harness/tests/conftest.py imports the same.
if "id_mask" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "id_mask", os.path.join(os.path.dirname(__file__), "harness", "tests", "id_mask.py"))
    sys.modules["id_mask"] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules["id_mask"])
pytest_make_parametrize_id = sys.modules["id_mask"].pytest_make_parametrize_id


@pytest.fixture(autouse=True)
def _isolate_recipe_store(monkeypatch, tmp_path):
    """wq#4288: fworker's real done/block path calls recipe_store.record_outcome(),
    which would write the live fleet_memory `recipes` table, or on fallback the live
    dispatcher/recipes.json. Force the JSON path and point it at a per-test file;
    tests that want PG opt back in explicitly."""
    monkeypatch.setenv("FOREMAN_RECIPE_PG", "off")
    monkeypatch.setenv("FOREMAN_RECIPE_STORE", str(tmp_path / "recipes.json"))
    rc = sys.modules.get("recipe_crystallizer")
    if rc is not None:
        monkeypatch.setattr(rc, "STORE_PATH", str(tmp_path / "recipes.json"))


@pytest.fixture(autouse=True)
def _isolate_minerva_db_root(monkeypatch, tmp_path):
    """wq#2784: fworker's real attempt-end path (episodic.commit_to_archive ->
    minerva_memory.py) wrote test episodes into prod minerva.db. Applies to every dir a
    `pytest` run collects; cheap (env vars only). wq#3222: this fixture sat untracked in
    the primary tree from 2026-09-26 and never landed; committed here."""
    prod_db = os.path.realpath("/home/lumen/minerva/minerva.db")
    prod_dir = os.path.realpath("/home/lumen/minerva/archival")
    monkeypatch.setenv("MINERVA_DB", str(tmp_path / "minerva_test.db"))
    monkeypatch.setenv("MINERVA_ARCHIVAL_DIR", str(tmp_path / "archival_test"))
    yield
    effective_db = os.path.realpath(
        os.environ.get("MINERVA_DB") or "/home/lumen/minerva/minerva.db")
    effective_dir = os.path.realpath(
        os.environ.get("MINERVA_ARCHIVAL_DIR") or "/home/lumen/minerva/archival")
    if effective_db == prod_db or effective_dir == prod_dir:
        pytest.fail(
            "MINERVA_DB/MINERVA_ARCHIVAL_DIR resolved to the PROD path during a test "
            f"(db={effective_db!r} dir={effective_dir!r}) -- wq#2784 pollution class."
        )
