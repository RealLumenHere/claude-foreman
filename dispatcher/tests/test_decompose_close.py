"""wq#2517 phase 3: a decomposed parent is deps-blocked while any child is open and
auto-closes at the Route gate when every child is done, notes naming the children.

Same zero-quota harness as test_decompose_dispatch.py: real `wq` against a scratch
WQ_DB, fake lumen-run recording every unit start. The done-watch is the wq#992 refill
kick (the last child's fworker kicks one dispatch pass on exit); every test here drives
run_pass() directly, which is exactly the pass that kick starts. Adversarial per
DESIGN §11: every no-spawn assertion checks the lumen-run call log, never just the
skip line.
"""
import os
import sqlite3
import sys
from types import SimpleNamespace

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
from test_dispatcher import (  # noqa: E402,F401
    add_ticket, db_exec, denv, fd, log_events, lumen_calls, notes_of, ntfy_lines,
    router, write_config)
from test_decompose_dispatch import decomposer_starts, route_to_decompose  # noqa: E402
from test_decompose_planner import fake_llm  # noqa: E402
import decomposer  # noqa: E402
import decompose_planner as dp  # noqa: E402


def child_detail(pid):
    return (f"a concrete filed sub-task with enough body\n\n"
            f"{decomposer.MARK_FROM}{pid}\n{decomposer.MARK_DEPTH}1\n")


def add_child(denv, pid, title, status="open"):
    # ghostlane is not in the dispatcher config, so a child is never itself a candidate
    cid = add_ticket(title, lane="ghostlane", detail=child_detail(pid))
    if status != "open":
        mark_done(denv, cid)
    return cid


def mark_done(denv, cid):
    db_exec(denv, "UPDATE tasks SET status='done', done_at=datetime('now') WHERE id=?",
            (cid,))


def link_deps(denv, pid, children):
    db_exec(denv, "UPDATE tasks SET deps=? WHERE id=?",
            (",".join(str(c) for c in children), pid))


def status_of(denv, tid):
    c = sqlite3.connect(denv["wq_db"])
    try:
        return c.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    finally:
        c.close()


# ---- a parent with open children is never dispatched -------------------------------

def test_parent_with_open_children_is_deps_blocked_never_dispatched(denv, monkeypatch):
    """The linked shape decompose() leaves behind: deps = children. The Deps gate
    (stage 6) holds the parent before the Route gate ever runs — wq --deps is blocking,
    not a link (fd.unmet_deps; wq's own claim path checks deps_met the same way)."""
    write_config(denv, decomposer={"enabled": True})
    pid = add_ticket("decomposed epic", model=None)
    kids = [add_child(denv, pid, "child a"), add_child(denv, pid, "child b")]
    link_deps(denv, pid, kids)
    route_to_decompose(monkeypatch, pid)
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=pid)
    assert len(skips) == 1
    assert skips[0]["stage"] == "deps" and skips[0]["reason"] == "deps_unmet"
    assert lumen_calls(denv) == []          # no fworker, no decomposer unit
    assert status_of(denv, pid) == "open"


def test_unlinked_parent_with_a_live_child_neither_respawns_nor_closes(denv, monkeypatch):
    """The wq#1141 half-filed shape (children exist, parent deps never linked): the
    marker alone still refuses a re-decomposition, and a mixed done/open set is not
    'all done', so nothing closes either."""
    write_config(denv, decomposer={"enabled": True})
    pid = add_ticket("half-linked epic", model=None)
    add_child(denv, pid, "finished child", status="done")
    add_child(denv, pid, "live child")
    route_to_decompose(monkeypatch, pid)
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=pid)
    assert len(skips) == 1 and skips[0]["reason"] == "decompose_has_children"
    assert decomposer_starts(denv) == [] and lumen_calls(denv) == []
    assert status_of(denv, pid) == "open"


# ---- the parent closes when the last child is done ----------------------------------

def test_parent_closes_when_all_children_done_with_note_listing_them(denv, monkeypatch):
    write_config(denv, decomposer={"enabled": True})
    pid = add_ticket("finished epic", model=None)
    kids = [add_child(denv, pid, "child a", status="done"),
            add_child(denv, pid, "child b", status="done")]
    link_deps(denv, pid, kids)
    route_to_decompose(monkeypatch, pid)
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=pid)
    assert len(skips) == 1
    assert skips[0]["stage"] == "decompose"
    assert skips[0]["reason"] == "decomposed_parent_closed"
    assert status_of(denv, pid) == "done"
    notes = notes_of(denv, pid)
    assert f"all {len(kids)} sub-tickets done" in notes
    for c in kids:
        assert f"wq#{c}" in notes
    assert decomposer_starts(denv) == [] and lumen_calls(denv) == []


def test_close_waits_for_the_last_child(denv, monkeypatch):
    """Two passes, as the wq#992 kicks would drive them: deps-blocked while one child
    is open, closed on the pass after the last child is done."""
    write_config(denv, decomposer={"enabled": True})
    pid = add_ticket("epic closing in stages", model=None)
    kids = [add_child(denv, pid, "child a", status="done"),
            add_child(denv, pid, "child b")]
    link_deps(denv, pid, kids)
    route_to_decompose(monkeypatch, pid)
    assert fd.run_pass() == 0
    assert log_events(denv, "skip", ticket=pid)[-1]["reason"] == "deps_unmet"
    assert status_of(denv, pid) == "open"
    mark_done(denv, kids[1])
    assert fd.run_pass() == 0
    assert log_events(denv, "skip", ticket=pid)[-1]["reason"] == "decomposed_parent_closed"
    assert status_of(denv, pid) == "done"


def test_dry_run_reports_the_close_but_leaves_the_parent_open(denv, monkeypatch):
    write_config(denv, decomposer={"enabled": True})
    pid = add_ticket("epic under dry-run", model=None)
    kids = [add_child(denv, pid, "child a", status="done"),
            add_child(denv, pid, "child b", status="done")]
    link_deps(denv, pid, kids)
    route_to_decompose(monkeypatch, pid)
    assert fd.run_pass(dry_run=True) == 0
    skips = log_events(denv, "skip", ticket=pid)
    assert len(skips) == 1 and skips[0]["reason"] == "would_close_decomposed_parent"
    assert status_of(denv, pid) == "open"


def test_dark_switch_never_closes(denv, monkeypatch):
    """Ships dark: with decomposer.enabled off the Route gate skips route_unassigned
    before any close logic — activation stays a deliberate reviewed step."""
    write_config(denv, decomposer={"enabled": False})
    pid = add_ticket("epic while dark", model=None)
    kids = [add_child(denv, pid, "child a", status="done")]
    link_deps(denv, pid, kids)
    route_to_decompose(monkeypatch, pid)
    assert fd.run_pass() == 0
    assert log_events(denv, "skip", ticket=pid)[0]["reason"] == "route_unassigned"
    assert status_of(denv, pid) == "open"


def test_refused_close_pages_and_leaves_the_parent_open(denv, monkeypatch):
    """Fail-open: a `wq done` refusal (deliverable gate, fence) pages and returns a
    skip — never a crashed pass, never a silently half-closed parent."""
    monkeypatch.setattr(fd, "WQ_BIN", "/bin/false")
    res = fd.close_decomposed_parent(SimpleNamespace(dry_run=False), 424242, [1, 2])
    assert res.reason == "decompose_close_failed"
    assert any("auto-close of decomposed parent wq#424242" in l
               for l in ntfy_lines(denv))


# ---- integration: DECOMPOSE ticket -> sub-tickets -> parent close (fake LLM) ---------

def test_decompose_ticket_through_children_to_parent_close(denv, monkeypatch):
    """The whole wq#1146 arc against the real wq CLI and dispatcher passes, with the
    LLM faked: the decomposer files critic-passed children through `wq add` and links
    the parent's deps; the parent is deps-blocked (never dispatched, never re-decomposed)
    while any child is open; the pass after the last child is done closes it with a
    note naming every child."""
    # wq#1676's liveness on-ramp is warn-only here: this test holds phase 3's tracking
    # honest, not wq's creation gates.
    monkeypatch.setenv("FOREMAN_LIVENESS_CLEAR", "0")
    write_config(denv, decomposer={"enabled": True})
    pid = add_ticket("epic: build the whole campaign", model=None,
                     detail="Build the whole campaign end to end.")
    route_to_decompose(monkeypatch, pid)

    # the decomposer unit the dispatcher would spawn, run in-process with a fake LLM.
    # PLAN's lanes (build, fleet) are real wq lanes but not in this test's dispatcher
    # config, so the children are never themselves candidates in run_pass().
    arch = {"standard-engineering", "mechanical-edit"}
    wq = dp.WqCli(db=denv["wq_db"])
    res = decomposer.decompose(
        wq.show(pid), wq=wq,
        plan_fn=dp.make_plan_fn(arch, {"build", "fleet"}, llm=fake_llm),
        critic_fn=dp.make_critic_fn(llm=fake_llm),
        valid_archetypes=arch, routes_to_decompose=lambda a: a not in arch,
        lanes={"build", "fleet"})
    assert res.status == "filed"
    assert decomposer.MIN_SUBS <= len(res.children) <= decomposer.MAX_SUBS

    # while any child is open: deps-blocked, nothing spawned, nothing re-decomposed
    assert fd.run_pass() == 0
    assert log_events(denv, "skip", ticket=pid)[-1]["reason"] == "deps_unmet"
    assert lumen_calls(denv) == []
    for c in res.children[:-1]:
        mark_done(denv, c)
    assert fd.run_pass() == 0
    assert log_events(denv, "skip", ticket=pid)[-1]["reason"] == "deps_unmet"
    assert status_of(denv, pid) == "open"

    # last child done -> the next pass (the one the wq#992 kick starts) closes it
    mark_done(denv, res.children[-1])
    assert fd.run_pass() == 0
    assert log_events(denv, "skip", ticket=pid)[-1]["reason"] == "decomposed_parent_closed"
    assert status_of(denv, pid) == "done"
    notes = notes_of(denv, pid)
    for c in res.children:
        assert f"wq#{c}" in notes
    assert decomposer_starts(denv) == [] and log_events(denv, "spawn") == []
