"""wq#2211 verifier probes against wq#2134 (PR 770): defects the ticket's own proofs miss.

Each test asserts the CORRECT behaviour and is `xfail(strict=True)` against the code as shipped;
wq#2212 fixes the code and deletes the marker (strict: an accidental fix without removing the
marker fails the suite). Recorded in ISSUE-LOG #509.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_gate_store import (  # noqa: E402,F401
    denv, add_ticket, db_exec, passes, gate_store, fd)


def spawned(events):
    return [e["ticket"] for e in events if e["event"] == "spawn"]


@pytest.mark.xfail(strict=True, reason="wq#2212: Deps parks a verdict that came from a failed "
                   "wq show; the fingerprint holds the real (done) dep status, so 1 h stuck")
def test_transient_dep_lookup_failure_must_not_park_a_dispatchable_ticket(denv, monkeypatch):
    dep = add_ticket("the dep")
    db_exec(denv, "UPDATE tasks SET status='done' WHERE id=?", (dep,))
    child = add_ticket("child of a done dep", deps=str(dep))
    real = fd.router.wq_show
    flaky = {"on": True}

    def wq_show(tid):
        if flaky["on"] and str(tid) == str(dep):
            raise SystemExit("router: no such ticket (transient wq failure)")
        return real(tid)
    monkeypatch.setattr(fd.router, "wq_show", wq_show)
    assert fd.run_pass() == 0
    flaky["on"] = False
    assert fd.run_pass() == 0
    assert spawned(passes(denv)[1]) == [child]


@pytest.mark.xfail(strict=True, reason="wq#2212: Route parks route_unassigned for 6 h when "
                   "router.assign raised SystemExit (any unparsable wq show --json)")
def test_transient_route_failure_must_not_park_six_hours(denv, monkeypatch):
    tid = add_ticket("plain ticket")
    db_exec(denv, "UPDATE tasks SET model=NULL, effort=NULL, archetype=NULL WHERE id=?", (tid,))
    real = fd.router.assign
    flaky = {"on": True}

    def assign(t, **kw):
        if flaky["on"]:
            raise SystemExit("router: no such ticket (transient)")
        return real(t, **kw)
    monkeypatch.setattr(fd.router, "assign", assign)
    assert fd.run_pass() == 0
    flaky["on"] = False
    assert fd.run_pass() == 0
    assert spawned(passes(denv)[1]) == [tid]


@pytest.mark.xfail(strict=True, reason="wq#2212: FOREMAN_GATE_STORE=off leaves rows_by_reason / "
                   "parked_rows feeding the janitor; dispatch_janitor.py's docstring says it empties them")
def test_lever_off_must_empty_the_janitor_inputs(denv, monkeypatch):
    add_ticket("[EPIC] tracker")
    assert fd.run_pass() == 0
    assert gate_store.by_reason() == {"nodispatch_marker": 1}
    monkeypatch.setenv("FOREMAN_GATE_STORE", "off")
    assert gate_store.rows_by_reason() == {}
    assert gate_store.parked_rows() == {}
