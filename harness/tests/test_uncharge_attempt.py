"""wq#1499: uncharge_attempt.py — the designed uncharge for attempt-N.json records the
classifier scored as a charged attempt when no model work ever ran.

Pinned to the real _runs/wq869/attempt-5.json shape (also #879, #983, #1122, #1350):
classification CRASH, turns 1, elapsed_s ~2, exit_code 1, result_subtype success. The
contract: rename to attempt-N.uncharged.json so self_heal.attempt_count() and
runaway.stuck_ticket_ids() stop counting it, keep the content as audit trail, never
delete, never touch a record that does not match the fault signature unless --force, and
never let fworker reuse the freed number (its usage/evidence sidecars are still on disk).
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "dispatcher"))
sys.path.insert(0, os.path.join(REPO, "harness"))

WQ869_ATTEMPT_5 = {
    "attempt": 5, "ticket": 869, "worker_id": "fworker-wq869-a5", "lane": "fleet",
    "classification": "CRASH", "raw_classification": "CRASH", "killed_reason": None,
    "turns": 1, "elapsed_s": 2.264770030975342, "exit_code": 1,
    "result_subtype": "success", "model": "opus-5",
}


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setenv("FOREMAN_RUNS_ROOT", str(tmp_path))
    for m in ("self_heal", "uncharge_attempt", "fworker", "runaway"):
        sys.modules.pop(m, None)
    import self_heal
    import uncharge_attempt
    import fworker
    monkeypatch.setattr(self_heal, "RUNS_ROOT", str(tmp_path))
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path))
    return tmp_path, self_heal, uncharge_attempt, fworker


def write(tmp_path, tid, n, rec):
    d = tmp_path / f"wq{tid}"
    d.mkdir(exist_ok=True)
    (d / f"attempt-{n}.json").write_text(json.dumps({**rec, "attempt": n}))
    return d


def test_fault_record_is_renamed_not_deleted_and_stops_counting(runs):
    tmp_path, self_heal, ua, fworker = runs
    d = write(tmp_path, 869, 5, WQ869_ATTEMPT_5)
    assert self_heal.attempt_count(869) == 1
    rc = ua.main(["869", "--attempt", "5", "--reason", "wq#1499 harness fault"])
    assert rc == 0
    assert not (d / "attempt-5.json").exists()
    kept = json.loads((d / "attempt-5.uncharged.json").read_text())
    assert kept["classification"] == "CRASH"
    assert kept["uncharged_reason"] == "wq#1499 harness fault"
    assert kept["uncharged_at"]
    assert self_heal.attempt_count(869) == 0


HANG = {"classification": "HANG", "turns": 0, "model": "qwen3-coder-30b", "exit_code": 1}


def test_bulk_rung_stamps_matching_records_in_place(runs, capsys):
    tmp_path, self_heal, ua, _ = runs
    d = write(tmp_path, 10, 1, HANG)
    write(tmp_path, 10, 2, {**HANG, "turns": 3})                      # model produced output
    write(tmp_path, 10, 3, {**HANG, "model": "claude-sonnet-5"})      # wrong model
    write(tmp_path, 11, 1, HANG)
    assert self_heal.attempt_count(10, charged_only=True) == 3

    assert ua.main(["--cause", "rung", "--model-prefix", "qwen"]) == 0

    assert json.loads((d / "attempt-1.json").read_text())["charged_to"] == "rung"
    assert "charged_to" not in json.loads((d / "attempt-2.json").read_text())
    assert "charged_to" not in json.loads((d / "attempt-3.json").read_text())
    assert self_heal.attempt_count(10, charged_only=True) == 2
    assert self_heal.attempt_count(10) == 3          # never deleted, never renamed
    assert self_heal.attempt_count(11, charged_only=True) == 0
    assert "touched 2 attempt record(s)" in capsys.readouterr().out


def test_bulk_rung_dry_run_prints_and_touches_nothing(runs, capsys):
    tmp_path, self_heal, ua, _ = runs
    d = write(tmp_path, 10, 1, HANG)
    before = (d / "attempt-1.json").read_text()

    assert ua.main(["--cause", "rung", "--model-prefix", "qwen", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "would charge to rung" in out and "would touch 1" in out
    assert (d / "attempt-1.json").read_text() == before


def test_bulk_rung_never_overwrites_another_charge_and_is_idempotent(runs):
    tmp_path, self_heal, ua, _ = runs
    d = write(tmp_path, 10, 1, {**HANG, "charged_to": "harness"})
    write(tmp_path, 10, 2, HANG)

    assert ua.main(["--cause", "rung", "--model-prefix", "qwen", "10"]) == 0
    assert ua.main(["--cause", "rung", "--model-prefix", "qwen", "10"]) == 0

    assert json.loads((d / "attempt-1.json").read_text())["charged_to"] == "harness"
    assert json.loads((d / "attempt-2.json").read_text())["charged_to"] == "rung"


def test_bulk_rung_requires_a_model_prefix(runs):
    _, _, ua, _ = runs
    assert ua.main(["--cause", "rung"]) == 2


def test_all_uncharges_only_fault_shaped_records(runs):
    tmp_path, self_heal, ua, fworker = runs
    real = {**WQ869_ATTEMPT_5, "turns": 40, "elapsed_s": 900.0, "classification": "FAILED"}
    d = write(tmp_path, 879, 1, real)
    for n in (2, 3, 4, 5):
        write(tmp_path, 879, n, WQ869_ATTEMPT_5)
    assert self_heal.attempt_count(879) == 5
    assert ua.main(["879", "--all", "--reason", "r"]) == 0
    assert (d / "attempt-1.json").exists()
    assert self_heal.attempt_count(879) == 1
    assert sorted(p.name for p in d.glob("attempt-*.uncharged.json")) == [
        f"attempt-{n}.uncharged.json" for n in (2, 3, 4, 5)]


def test_non_fault_record_is_refused_without_force(runs):
    tmp_path, self_heal, ua, fworker = runs
    d = write(tmp_path, 10, 1, {**WQ869_ATTEMPT_5, "turns": 40, "elapsed_s": 500.0})
    assert ua.main(["10", "--attempt", "1", "--reason", "r"]) == 1
    assert (d / "attempt-1.json").exists()
    assert ua.main(["10", "--attempt", "1", "--reason", "r", "--force"]) == 0
    assert (d / "attempt-1.uncharged.json").exists()


@pytest.mark.parametrize("field,value", [
    ("exit_code", 0), ("exit_code", None), ("turns", 2), ("elapsed_s", 12.0),
    ("classification", "SUCCESS"), ("classification", "LIMIT"),
])
def test_signature_guard_rejects_each_missing_signal(runs, field, value):
    tmp_path, self_heal, ua, fworker = runs
    assert ua.looks_like_harness_fault({**WQ869_ATTEMPT_5, field: value}) is False
    assert ua.looks_like_harness_fault(WQ869_ATTEMPT_5) is True


def test_dry_run_changes_nothing(runs):
    tmp_path, self_heal, ua, fworker = runs
    d = write(tmp_path, 983, 1, WQ869_ATTEMPT_5)
    assert ua.main(["983", "--all", "--reason", "r", "--dry-run"]) == 0
    assert (d / "attempt-1.json").exists()
    assert not (d / "attempt-1.uncharged.json").exists()


def test_five_fault_attempts_drop_below_the_runaway_threshold(runs):
    # runaway.stuck_ticket_ids() reads self_heal.attempt_count() (RUNAWAY_MAX_ATTEMPTS=5);
    # this is the wq869/#879 shape that parked both tickets forever.
    tmp_path, self_heal, ua, fworker = runs
    for n in range(1, 6):
        write(tmp_path, 869, n, WQ869_ATTEMPT_5)
    assert self_heal.attempt_count(869) == 5
    assert ua.main(["869", "--all", "--reason", "r"]) == 0
    assert self_heal.attempt_count(869) == 0


def test_fworker_never_reuses_an_uncharged_number(runs):
    tmp_path, self_heal, ua, fworker = runs
    for n in range(1, 6):
        write(tmp_path, 1122, n, WQ869_ATTEMPT_5)
    assert fworker.next_attempt_n(1122) == 6
    assert ua.main(["1122", "--all", "--reason", "r"]) == 0
    assert self_heal.attempt_count(1122) == 0
    # usage-5.json / evidence/attempt-5 for the uncharged runs are still on disk; a new
    # attempt must be 6, not 1, or it overwrites that audit trail.
    assert fworker.next_attempt_n(1122) == 6


def test_no_records_is_an_error(runs):
    tmp_path, self_heal, ua, fworker = runs
    assert ua.main(["4242", "--all", "--reason", "r"]) == 1
