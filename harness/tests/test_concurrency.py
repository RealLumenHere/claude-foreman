"""concurrency.py — cloud-worker slot accounting. BUILD_PLAN.md §T2b (wq#616).
Zero quota: a fake `lumen-run list` shim, no real systemd/claude involved.
"""
import json
import os
import stat
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HARNESS_DIR)
import concurrency  # noqa: E402


def install_fake_lumen_run(tmp_path):
    """Writes an executable `lumen-run` wrapper under tmp_path/bin and returns its path
    (for direct FOREMAN_LUMEN_RUN_BIN / concurrency.LUMEN_RUN_BIN use)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    wrapper = bin_dir / "lumen-run"
    wrapper.write_text(
        f"#!/usr/bin/env bash\nexec python3 {TESTS_DIR}/fake_lumen_run.py \"$@\"\n")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(wrapper)


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    lumen_run = install_fake_lumen_run(tmp_path)
    state_file = tmp_path / "units.json"
    state_file.write_text("[]")
    monkeypatch.setenv("FAKE_LUMEN_RUN_STATE", str(state_file))
    concurrency.LUMEN_RUN_BIN = lumen_run
    return state_file


def seed_units(state_file, units):
    state_file.write_text(json.dumps(units))


def test_no_units_running_reports_zero(fake_env):
    units = concurrency.list_running_units()
    assert units == []


def test_matching_prefix_units_counted(fake_env):
    seed_units(fake_env, ["fworker-wq616", "fworker-wq648", "mv4-land"])
    units = concurrency.list_running_units(prefix="fworker-wq")
    assert sorted(units) == ["fworker-wq616", "fworker-wq648"]


def test_ticket_id_with_trailing_digits_parsed_correctly(fake_env):
    # fworker-wq616's own slug ends in digits right before the lumen-run epoch suffix
    # (fworker-wq616-<epoch>.service) — the non-greedy regex must not truncate the name
    # at the ticket id's own digits.
    seed_units(fake_env, ["fworker-wq616"])
    units = concurrency.list_running_units(prefix="fworker-wq")
    assert units == ["fworker-wq616"]


def test_slots_available_math(fake_env):
    seed_units(fake_env, ["fworker-wq1", "fworker-wq2"])
    avail, units = concurrency.slots_available(ceiling=3)
    assert avail == 1
    assert len(units) == 2


def test_slots_available_clamped_at_zero_when_over_ceiling(fake_env):
    seed_units(fake_env, ["fworker-wq1", "fworker-wq2", "fworker-wq3", "fworker-wq4"])
    avail, units = concurrency.slots_available(ceiling=3)
    assert avail == 0


def test_check_cli_exit_code_room(fake_env, capsys):
    seed_units(fake_env, ["fworker-wq1"])
    import argparse
    args = argparse.Namespace(prefix="fworker-wq", ceiling=3)
    rc = concurrency.cmd_check(args)
    assert rc == 0
    decision = json.loads(capsys.readouterr().out)
    assert decision["available"] == 2


def test_check_cli_exit_code_full(fake_env, capsys):
    seed_units(fake_env, ["fworker-wq1", "fworker-wq2", "fworker-wq3"])
    import argparse
    args = argparse.Namespace(prefix="fworker-wq", ceiling=3)
    rc = concurrency.cmd_check(args)
    assert rc == 1


def test_lumen_run_unreachable_raises(monkeypatch):
    concurrency.LUMEN_RUN_BIN = "/nonexistent/lumen-run-binary"
    with pytest.raises(concurrency.LumenRunError):
        concurrency.list_running_units()


# ---------- T4 (wq#618): local-rung units excluded from the cloud ceiling ----------

def fake_model_lookup(models):
    return lambda ticket_id: models.get(ticket_id)


def test_is_local_unit_true_for_a_known_local_route():
    lookup = fake_model_lookup({"1": "deepseek-v4-flash"})
    assert concurrency.is_local_unit("fworker-wq1", lookup) is True


def test_is_local_unit_false_for_a_cloud_model():
    lookup = fake_model_lookup({"1": "claude-sonnet-5"})
    assert concurrency.is_local_unit("fworker-wq1", lookup) is False


def test_is_local_unit_fails_safe_to_cloud_on_unresolvable_ticket():
    lookup = fake_model_lookup({})  # ticket "1" not in the map -> None
    assert concurrency.is_local_unit("fworker-wq1", lookup) is False


def test_is_local_unit_false_for_a_non_ticket_unit_name():
    lookup = fake_model_lookup({"1": "deepseek-v4-flash"})
    assert concurrency.is_local_unit("mv4-land", lookup) is False


def test_slots_available_excludes_local_units_when_asked(fake_env):
    seed_units(fake_env, ["fworker-wq1", "fworker-wq2", "fworker-wq3"])
    lookup = fake_model_lookup({"1": "claude-sonnet-5", "2": "claude-sonnet-5",
                                 "3": "deepseek-v4-flash"})
    avail, units = concurrency.slots_available(ceiling=3, exclude_local=True, model_lookup=lookup)
    # 3 units running, but only 2 count toward the cloud ceiling -> 1 slot free.
    assert avail == 1
    assert len(units) == 2


def test_slots_available_counts_local_units_by_default(fake_env):
    seed_units(fake_env, ["fworker-wq1", "fworker-wq2", "fworker-wq3"])
    avail, units = concurrency.slots_available(ceiling=3)
    assert avail == 0
    assert len(units) == 3
