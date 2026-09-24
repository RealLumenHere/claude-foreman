"""wq#2516 phase 2: DECOMPOSE-routed tickets go to the decomposer, behind config.

Same zero-quota harness as test_dispatcher.py: the real `wq` against a scratch WQ_DB,
fake lumen-run recording every unit start. router.assign is stubbed to put a ticket on
the DECOMPOSE rung — the router's own DECOMPOSE selection is test_router.py's to prove.
Adversarial per DESIGN §11: every no-spawn assertion checks the lumen-run call log,
never just the skip line.
"""
import json
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
from test_dispatcher import (  # noqa: E402,F401
    add_ticket, denv, fd, log_events, lumen_calls, ntfy_lines, router, write_config)
import decomposer  # noqa: E402

REPO = os.path.dirname(os.path.dirname(TESTS_DIR))


def route_to_decompose(monkeypatch, *tids):
    ids = {int(t) for t in tids}

    def fake_assign(tid, **kwargs):
        assert int(tid) in ids, f"unexpected router.assign for wq#{tid}"
        return {"rung": {"model": "DECOMPOSE", "effort": None}, "reasons": []}
    monkeypatch.setattr(router, "assign", fake_assign)


def decomposer_starts(denv):
    return [c for c in lumen_calls(denv) if c["args"][:1] == ["start"]
            and any(a.startswith(router.DECOMPOSER_UNIT_PREFIX) for a in c["args"])]


def test_shipped_config_keeps_decomposer_dark():
    """Activation is a separate reviewed step: the committed block exists and is OFF."""
    with open(os.path.join(REPO, "dispatcher", "config.json")) as f:
        cfg = json.load(f)
    assert cfg["decomposer"]["enabled"] is False


def test_candidate_model_distinguishes_decompose(denv, monkeypatch):
    tid = add_ticket("epic", model=None)
    route_to_decompose(monkeypatch, tid)
    assert fd.candidate_model({"model": None}, tid) == (None, fd.ROUTE_DECOMPOSE)
    monkeypatch.setattr(router, "assign",
                        lambda t, **k: {"rung": None, "reasons": ["no rung"]})
    assert fd.candidate_model({"model": None}, tid) == (None, "route_unassigned")


def test_switch_off_skips_exactly_as_before(denv, monkeypatch):
    """Default config has no live switch: the DECOMPOSE ticket skips route_unassigned
    and nothing spawns — byte-for-byte the pre-wq#2516 decision."""
    tid = add_ticket("epic parks while dark", model=None)
    route_to_decompose(monkeypatch, tid)
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1
    assert skips[0]["stage"] == "budget" and skips[0]["reason"] == "route_unassigned"
    assert lumen_calls(denv) == []


def test_block_present_but_disabled_skips(denv, monkeypatch):
    write_config(denv, decomposer={"enabled": False, "max_per_pass": 1})
    tid = add_ticket("epic, switch explicitly off", model=None)
    route_to_decompose(monkeypatch, tid)
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "route_unassigned"
    assert lumen_calls(denv) == []


def test_switch_on_spawns_decomposer(denv, monkeypatch):
    write_config(denv, decomposer={"enabled": True})
    tid = add_ticket("epic goes to the decomposer", model=None)
    route_to_decompose(monkeypatch, tid)
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1
    assert skips[0]["stage"] == "decompose" and skips[0]["reason"] == "decomposer_spawned"
    starts = decomposer_starts(denv)
    assert len(starts) == 1
    args = starts[0]["args"]
    assert f"{router.DECOMPOSER_UNIT_PREFIX}{tid}" in args
    assert any(a.endswith("decompose_planner.py") for a in args)
    assert "--ticket" in args and str(tid) in args
    assert log_events(denv, "spawn") == []  # the parent itself never gets an fworker
    ends = log_events(denv, "pass_end")
    assert len(ends) == 1 and ends[0]["decomposer_spawned"] == 1


def test_running_decomposer_not_respawned(denv, monkeypatch):
    write_config(denv, decomposer={"enabled": True})
    tid = add_ticket("epic", model=None)
    route_to_decompose(monkeypatch, tid)
    assert fd.run_pass() == 0
    assert len(decomposer_starts(denv)) == 1
    # fake lumen-run keeps decomposer-wq<tid> 'running' in its state file
    assert fd.run_pass() == 0
    assert len(decomposer_starts(denv)) == 1
    assert log_events(denv, "skip", ticket=tid)[-1]["reason"] == "decomposer_running"


def test_never_redecomposes_parent_with_children(denv, monkeypatch):
    write_config(denv, decomposer={"enabled": True})
    tid = add_ticket("epic already split", model=None)
    # ghostlane is not in the dispatcher config, so the child is never itself a candidate
    add_ticket("child one", lane="ghostlane",
               detail=f"a concrete filed sub-task with enough body\n\n"
                      f"{decomposer.MARK_FROM}{tid}\n{decomposer.MARK_DEPTH}1\n")
    route_to_decompose(monkeypatch, tid)
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "decompose_has_children"
    assert decomposer_starts(denv) == []


def test_marker_never_matches_prefix_ticket_ids(denv, monkeypatch):
    """wq#900 must not read as a child of wq#90: the marker match is newline-anchored."""
    write_config(denv, decomposer={"enabled": True})
    tid = add_ticket("epic with a near-miss marker elsewhere", model=None)
    add_ticket("child of a LONGER id", lane="ghostlane",
               detail=f"a concrete filed sub-task with enough body\n\n"
                      f"{decomposer.MARK_FROM}{tid}9\n{decomposer.MARK_DEPTH}1\n")
    route_to_decompose(monkeypatch, tid)
    assert fd.run_pass() == 0
    assert log_events(denv, "skip", ticket=tid)[0]["reason"] == "decomposer_spawned"


def test_depth_marked_parent_refused(denv, monkeypatch):
    """Recursion guard: a child that somehow routes back to DECOMPOSE never spawns."""
    write_config(denv, decomposer={"enabled": True})
    tid = add_ticket("a child mis-routed back to DECOMPOSE", model=None,
                     detail=f"work\n\n{decomposer.MARK_FROM}999999\n"
                            f"{decomposer.MARK_DEPTH}1\n")
    route_to_decompose(monkeypatch, tid)
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "decompose_depth"
    assert decomposer_starts(denv) == []


def test_per_pass_cap(denv, monkeypatch):
    write_config(denv, decomposer={"enabled": True, "max_per_pass": 1})
    t1 = add_ticket("first epic", model=None)
    t2 = add_ticket("second epic", model=None)
    route_to_decompose(monkeypatch, t1, t2)
    assert fd.run_pass() == 0
    assert len(decomposer_starts(denv)) == 1
    reasons = sorted(e["reason"] for e in log_events(denv, "skip"))
    assert reasons == ["decomposer_cap", "decomposer_spawned"]


def test_dry_run_spawns_nothing(denv, monkeypatch):
    write_config(denv, decomposer={"enabled": True})
    tid = add_ticket("epic", model=None)
    route_to_decompose(monkeypatch, tid)
    assert fd.run_pass(dry_run=True) == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "would_spawn_decomposer"
    assert lumen_calls(denv) == []


def test_spawn_failure_logged_and_notified(denv, monkeypatch):
    write_config(denv, decomposer={"enabled": True})
    tid = add_ticket("epic", model=None)
    route_to_decompose(monkeypatch, tid)
    monkeypatch.setattr(router, "decomposer_cmd", lambda t: ["false"])
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "decomposer_spawn_failed"
    assert any("decomposer spawn failed" in l for l in ntfy_lines(denv))
    ends = log_events(denv, "pass_end")
    assert len(ends) == 1 and ends[0]["decomposer_spawned"] == 0


def test_malformed_decomposer_block_fails_closed(denv, monkeypatch):
    write_config(denv, decomposer={"enabled": "yes"})
    add_ticket("never evaluated under a broken config", model=None)
    assert fd.run_pass() == 1
    errs = log_events(denv, "error")
    assert any("decomposer.enabled" in str(e.get("reason")) for e in errs)
    assert lumen_calls(denv) == []
