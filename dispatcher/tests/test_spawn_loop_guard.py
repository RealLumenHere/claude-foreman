"""spawn_loop_guard.py — restart-loop breaker unit tests (wq#1703).

Zero quota, zero fleet state: attempt-N.json fixtures are hand-written stubs under a
scratch RUNS_ROOT (the guard only reads self_heal.attempt_count()'s file COUNT), and the
aq binary is a fake shell script — a test must never open a row on the live queue.
"""
import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, DISPATCHER_DIR)
import spawn_loop_guard as slg  # noqa: E402
import self_heal                # noqa: E402

TID = 1543  # the ticket in ISSUE-LOG #283 that this guard exists for


@pytest.fixture
def genv(tmp_path, monkeypatch):
    runs_root = tmp_path / "_runs"
    runs_root.mkdir()
    monkeypatch.setattr(self_heal, "RUNS_ROOT", str(runs_root))
    monkeypatch.setattr(slg, "SPAWN_LOOP_MAX", 3)
    monkeypatch.setattr(slg, "SPAWN_LOOP_WINDOW_H", 3)
    return {"tmp": tmp_path, "runs_root": runs_root}


def now():
    return datetime.now(timezone.utc)


def make_attempts(genv, tid, n):
    d = genv["runs_root"] / f"wq{tid}"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        (d / f"attempt-{i}.json").write_text("{}")


def spawns(tid, ages_min, attempts=0):
    """A ledger with one entry per age (minutes ago), each recording `attempts`."""
    n = now()
    return {str(tid): [{"ts": (n - timedelta(minutes=m)).isoformat(timespec="seconds"),
                        "attempts": attempts} for m in ages_min]}


def state(ledger):
    return {slg.LEDGER_KEY: ledger}


# ---- record_spawn --------------------------------------------------------------------

def test_record_spawn_stamps_the_attempt_count_at_spawn_time(genv):
    make_attempts(genv, TID, 2)
    st = {}

    entries = slg.record_spawn(st, TID)

    assert len(entries) == 1
    assert entries[0]["attempts"] == 2
    assert st[slg.LEDGER_KEY][str(TID)] is entries
    datetime.fromisoformat(entries[0]["ts"])  # parseable, tz-aware


def test_record_spawn_appends_rather_than_replacing(genv):
    st = state(spawns(TID, [30]))

    slg.record_spawn(st, TID)

    assert len(st[slg.LEDGER_KEY][str(TID)]) == 2


# ---- looping -------------------------------------------------------------------------

def test_below_threshold_is_not_looping(genv):
    assert slg.looping(state(spawns(TID, [5, 60])), TID) is None


def test_threshold_spawns_with_no_finished_attempt_is_looping(genv):
    info = slg.looping(state(spawns(TID, [5, 40, 90])), TID)

    assert info["spawns"] == 3
    assert info["attempts"] == 0
    # oldest spawn in the window, so the message names when the burst started
    assert info["since"] < now().isoformat(timespec="seconds")


def test_a_finished_attempt_during_the_burst_clears_the_guard(genv):
    """The whole point: spawns that PRODUCE something are not a loop. The baseline was
    0 attempts; one finished since, so the next spawn is an honest retry."""
    ledger = spawns(TID, [5, 40, 90], attempts=0)
    make_attempts(genv, TID, 1)

    assert slg.looping(state(ledger), TID) is None


def test_a_ticket_that_was_already_being_retried_needs_fresh_progress(genv):
    """A ticket with 4 finished attempts before the burst is looping again unless the
    count moves past that baseline — not "it has attempts, therefore it's fine"."""
    make_attempts(genv, TID, 4)
    ledger = spawns(TID, [5, 40, 90], attempts=4)

    assert slg.looping(state(ledger), TID)["attempts"] == 4

    make_attempts(genv, TID, 5)
    assert slg.looping(state(ledger), TID) is None


def test_spawns_older_than_the_window_do_not_count(genv):
    ledger = spawns(TID, [10, 200, 400])  # 200/400 min > 3h window

    assert slg.looping(state(ledger), TID) is None


def test_window_and_threshold_are_overridable(genv):
    ledger = spawns(TID, [10, 200, 400])

    assert slg.looping(state(ledger), TID, window_h=24)["spawns"] == 3
    assert slg.looping(state(ledger), TID, threshold=2) is None
    assert slg.looping(state(ledger), TID, threshold=2, window_h=24)["spawns"] == 3


def test_no_ledger_at_all_is_not_looping(genv):
    assert slg.looping({}, TID) is None
    assert slg.looping({slg.LEDGER_KEY: {}}, 99) is None


def test_int_and_str_ticket_ids_hit_the_same_row(genv):
    """State round-trips through JSON, so the ledger's keys are always strings while
    callers hold ints."""
    st = state(spawns(TID, [5, 40, 90]))

    assert slg.looping(st, TID)["spawns"] == 3
    assert slg.looping(st, str(TID))["spawns"] == 3


# ---- prune_ledger --------------------------------------------------------------------

def test_prune_drops_old_entries_and_empty_tickets(genv):
    ledger = spawns(TID, [5, 200])
    ledger.update(spawns(999, [500]))

    pruned = slg.prune_ledger(ledger)

    assert list(pruned) == [str(TID)]
    assert len(pruned[str(TID)]) == 1


def test_prune_survives_junk_entries(genv):
    ledger = {str(TID): ["not-a-dict", {"no": "ts"}, {"ts": "gibberish"},
                         {"ts": now().isoformat(timespec="seconds"), "attempts": 0}]}

    assert len(slg.prune_ledger(ledger)[str(TID)]) == 1


def test_prune_of_nothing_is_empty(genv):
    assert slg.prune_ledger(None) == {}


def test_recent_returns_oldest_first(genv):
    ledger = spawns(TID, [5, 90, 40])

    tss = [e["ts"] for e in slg.recent(ledger, TID)]
    assert tss == sorted(tss)


# ---- aq_row --------------------------------------------------------------------------

def fake_aq(tmp_path, log, rc=0):
    p = tmp_path / "aq"
    p.write_text("#!/usr/bin/env bash\n"
                 f'printf "%s\\n" "$*" >> {log}\n'
                 f"exit {rc}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(p)


def test_aq_row_files_one_row_with_a_stable_dedup_key(genv):
    log = genv["tmp"] / "aq.log"
    aq = fake_aq(genv["tmp"], log)
    info = slg.looping(state(spawns(TID, [5, 40, 90])), TID)

    assert slg.aq_row(TID, info, aq_bin=aq) is True

    line = log.read_text().strip()
    assert line.startswith(f"add {slg.AQ_SOURCE} spawn-loop-wq{TID} ")
    assert "3 spawns" in line and "no finished attempt" in line


def test_aq_row_is_fail_open(genv):
    """A missing binary or a failing call must never break the dispatch pass — the skip
    has already happened by the time the row is filed."""
    assert slg.aq_row(TID, {"spawns": 3, "since": "x", "attempts": 0},
                      aq_bin=str(genv["tmp"] / "no-such-aq")) is False

    log = genv["tmp"] / "aq.log"
    aq = fake_aq(genv["tmp"], log, rc=1)
    assert slg.aq_row(TID, {"spawns": 3, "since": "x", "attempts": 0}, aq_bin=aq) is False


# ---- CLI -----------------------------------------------------------------------------

def test_list_looping_reads_the_live_state_file(genv, capsys):
    st = state(spawns(TID, [5, 40, 90]))
    st[slg.LEDGER_KEY].update(spawns(999, [5]))  # one spawn: not looping
    path = genv["tmp"] / "dstate.json"
    path.write_text(json.dumps(st))

    assert slg.main(["--state", str(path), "list-looping"]) == 0

    out = json.loads(capsys.readouterr().out)
    assert list(out) == [str(TID)] and out[str(TID)]["spawns"] == 3


def test_list_looping_on_a_missing_state_file_is_empty(genv, capsys):
    assert slg.main(["--state", str(genv["tmp"] / "gone.json"), "list-looping"]) == 0
    assert json.loads(capsys.readouterr().out) == {}
