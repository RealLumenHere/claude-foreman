"""wq#4235: crystallize/freeze pattern — classify, streak, freeze, unfreeze, lookup."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import recipe_crystallizer as rc  # noqa: E402


@pytest.fixture(autouse=True)
def _scratch_store(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "STORE_PATH", str(tmp_path / "recipes.json"))
    monkeypatch.setattr(rc, "FREEZE_THRESHOLD", 3)


def test_classify_ticket_type_hits_each_starter_class():
    assert rc.classify_ticket_type("fix the flaky test in test_foo.py") == "test-fix"
    assert rc.classify_ticket_type("bump pin in models.yaml config") == "config-edit"
    assert rc.classify_ticket_type("refactor the dispatcher's candidate_model") == "code-refactor"


def test_classify_ticket_type_outside_starter_set_is_none():
    assert rc.classify_ticket_type("stand up a new Grafana dashboard") is None


def test_record_outcome_noop_for_untracked_type():
    assert rc.record_outcome(None, "claude-sonnet-5", "worker.md", True) is None
    assert rc.record_outcome("rca-investigation", "claude-sonnet-5", "worker.md", True) is None


def test_freeze_after_n_consecutive_successes():
    for _ in range(2):
        result = rc.record_outcome("test-fix", "claude-sonnet-5", "worker.md", True)
        assert "frozen_at" not in result
    assert rc.lookup_recipe("test-fix") is None
    result = rc.record_outcome("test-fix", "claude-sonnet-5", "worker.md", True)
    assert result["model"] == "claude-sonnet-5"
    assert result["streak_at_freeze"] == 3
    frozen = rc.lookup_recipe("test-fix")
    assert frozen["model"] == "claude-sonnet-5"
    assert frozen["prompt_template"] == "worker.md"
    assert rc.is_frozen("test-fix")


def test_a_failing_streak_never_freezes():
    rc.record_outcome("config-edit", "claude-sonnet-5", "worker.md", True)
    rc.record_outcome("config-edit", "claude-sonnet-5", "worker.md", False)
    rc.record_outcome("config-edit", "claude-sonnet-5", "worker.md", True)
    rc.record_outcome("config-edit", "claude-sonnet-5", "worker.md", True)
    assert rc.lookup_recipe("config-edit") is None


def test_frozen_recipe_unfreezes_on_a_single_failure():
    for _ in range(3):
        rc.record_outcome("code-refactor", "claude-opus-5-5", "worker.md", True)
    assert rc.is_frozen("code-refactor")
    result = rc.record_outcome("code-refactor", "claude-opus-5-5", "worker.md", False)
    assert result["streak"] == 0
    assert rc.lookup_recipe("code-refactor") is None
    # re-exploration starts the streak over, not resuming from streak_at_freeze
    rc.record_outcome("code-refactor", "claude-opus-5-5", "worker.md", True)
    rc.record_outcome("code-refactor", "claude-opus-5-5", "worker.md", True)
    assert rc.lookup_recipe("code-refactor") is None
    rc.record_outcome("code-refactor", "claude-opus-5-5", "worker.md", True)
    assert rc.is_frozen("code-refactor")


def test_a_different_cell_does_not_share_the_frozen_cells_streak():
    for _ in range(3):
        rc.record_outcome("test-fix", "claude-sonnet-5", "worker.md", True)
    assert rc.lookup_recipe("test-fix")["model"] == "claude-sonnet-5"
    # a losing streak on a different (model, prompt_template) cell for the same
    # ticket_type must not touch the already-frozen cell above
    rc.record_outcome("test-fix", "claude-opus-5-5", "local_mechanical-edit.md", False)
    assert rc.lookup_recipe("test-fix")["model"] == "claude-sonnet-5"


def test_store_is_durable_across_fresh_loads():
    rc.record_outcome("test-fix", "claude-sonnet-5", "worker.md", True)
    rc.record_outcome("test-fix", "claude-sonnet-5", "worker.md", True)
    rc.record_outcome("test-fix", "claude-sonnet-5", "worker.md", True)
    store = rc.load_store()
    assert store["frozen"]["test-fix"]["model"] == "claude-sonnet-5"


def test_load_store_fails_open_on_corrupt_file(tmp_path, monkeypatch):
    bad = tmp_path / "corrupt.json"
    bad.write_text("{not json")
    monkeypatch.setattr(rc, "STORE_PATH", str(bad))
    assert rc.load_store() == {"cells": {}, "frozen": {}}
    assert rc.lookup_recipe("test-fix") is None
