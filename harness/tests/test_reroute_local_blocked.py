"""wq#2597 — the sweep that finishes wq#2583's AC part 2.

The bug this file exists to prevent is not "the sweep crashes"; it is "the sweep unblocks a
ticket whose block had nothing to do with the local rung", and "the sweep touches a block the
AC excluded". Both are asserted directly. No live queue.db is opened: every test builds its
own rows, and the wq CLI is replaced by a recorder.
"""
import os
import sqlite3
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import reroute_local_blocked as rlb  # noqa: E402

SCHEMA = ("CREATE TABLE tasks(id INTEGER PRIMARY KEY, model TEXT, effort TEXT, "
          "archetype TEXT, status TEXT, block_reason TEXT, claimed_by TEXT, pri INTEGER, "
          "title TEXT)")


def make_db(tmp_path, rows):
    path = str(tmp_path / "queue.db")
    con = sqlite3.connect(path)
    con.execute(SCHEMA)
    con.executemany(
        "INSERT INTO tasks(id, model, effort, archetype, status, block_reason, claimed_by, "
        "pri, title) VALUES(:id,:model,:effort,:archetype,:status,:block_reason,:claimed_by,"
        ":pri,:title)",
        [dict({"model": "local-worker", "effort": "", "archetype": "mechanical-edit",
               "status": "blocked", "block_reason": "UNKNOWN: HANG", "claimed_by": "",
               "pri": 2, "title": "t"}, **r) for r in rows])
    con.commit()
    con.close()
    return path


@pytest.mark.parametrize("reason,protected", [
    ("REAL_BLOCK: upstream API is gone", True),
    ("needs FRANK: mint a credential", True),
    ("CAPABILITY: no repo() tag for /home/lumen/questfree", True),
    ("CITED_PATH: evidence cites a path outside the diff", True),
    ("UNKNOWN: FALSE_COMPLETE: no mechanical signature matched", False),
    ("SPAWN_LOOP: no release note", False),
    ("evidence gate: 8 attempts, still unproven (AC1).", False),
    ("BLOCKED on a missing spec: pilots/1b7e443bb0fa/CARD.md", False),
])
def test_protected_blocks_are_the_ac_four(reason, protected):
    assert rlb.is_protected(reason) is protected


def test_candidates_skips_protected_and_non_local(tmp_path):
    db = make_db(tmp_path, [
        {"id": 1},                                             # in
        {"id": 2, "block_reason": "REAL_BLOCK: dead vendor"},   # out: protected
        {"id": 3, "model": "claude-sonnet-5"},                  # out: already cloud
        {"id": 4, "status": "open"},                            # out: not blocked
        {"id": 5, "model": "hathor-llama8b"},                   # in
    ])
    assert [c["id"] for c in rlb.candidates(db=db)] == [1, 5]
    assert rlb.liveness_count(db=db) == 2


def test_execution_block_is_rerouted_and_unblocked():
    plan = rlb.classify({"id": 7, "model": "hathor-llama8b", "archetype": "verify-pass",
                         "block_reason": "SPAWN_LOOP: no release note",
                         "claimed_by": "fworker-wq7"})
    assert (plan["model"], plan["effort"]) == ("claude-sonnet-5", "high")
    assert plan["unblock"] is True
    # the local session died holding the claim; the next dispatch cannot claim past it
    assert plan["release"] is True


def test_block_about_the_work_is_rerouted_but_stays_blocked():
    """wq#1953 live: re-routing cannot make a missing spec appear, so unblocking it would
    only spend a slot to re-derive the same block."""
    plan = rlb.classify({"id": 1953, "model": "local-worker", "archetype": "mechanical-edit",
                         "block_reason": "BLOCKED on a missing spec: pilots/x/CARD.md",
                         "claimed_by": "orch-burn-4"})
    assert plan["model"] == "claude-sonnet-5"
    assert plan["unblock"] is False
    assert plan["release"] is False
    assert plan["keep_blocked_because"]


def test_unset_archetype_leaves_effort_to_the_router():
    plan = rlb.classify({"id": 8, "model": "local-worker", "archetype": "",
                         "block_reason": "UNKNOWN: CRASH", "claimed_by": ""})
    assert plan["model"] == "claude-sonnet-5"
    assert plan["effort"] is None


def test_unmapped_archetype_raises_instead_of_guessing():
    with pytest.raises(KeyError):
        rlb.target_for("rca-investigation")


def test_apply_reroutes_before_unblocking(monkeypatch):
    """A row that is open and still pointed at the local rung is claimable by the dispatcher
    on the very rung this sweep exists to remove, so the model edit has to land first."""
    calls = []

    class P:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(rlb, "_wq", lambda args, agent: (calls.append(list(args)), P())[1])
    ok, _ = rlb.apply_plan(rlb.classify(
        {"id": 9, "model": "local-worker", "archetype": "mechanical-edit",
         "block_reason": "UNKNOWN: HANG", "claimed_by": "fworker-wq9"}),
        "test-agent", log=lambda _m: None)
    assert ok
    assert calls == [["edit", 9, "--model", "claude-sonnet-5", "--effort", "low"],
                     ["edit", 9, "--status", "open"],
                     ["release", 9]]


def test_apply_stops_when_the_model_edit_is_refused(monkeypatch):
    """Never unblock a row whose re-route did not take — that is the mis-routed-and-open
    state the ordering above is protecting."""
    class Refused:
        returncode, stdout, stderr = 3, "", "REFUSED: no such task"

    calls = []
    monkeypatch.setattr(rlb, "_wq",
                        lambda args, agent: (calls.append(list(args)), Refused())[1])
    ok, steps = rlb.apply_plan(rlb.classify(
        {"id": 10, "model": "local-worker", "archetype": "mechanical-edit",
         "block_reason": "UNKNOWN: HANG", "claimed_by": ""}), "a", log=lambda _m: None)
    assert ok is False
    assert len(calls) == 1
    assert "REFUSED" in steps[-1]


def test_liveness_reaches_zero_once_rows_carry_a_cloud_model(tmp_path):
    db = make_db(tmp_path, [{"id": 1}, {"id": 2, "model": "qwen36-local"}])
    assert rlb.liveness_count(db=db) == 2
    con = sqlite3.connect(db)
    con.execute("UPDATE tasks SET model='claude-sonnet-5'")
    con.commit()
    con.close()
    assert rlb.liveness_count(db=db) == 0
