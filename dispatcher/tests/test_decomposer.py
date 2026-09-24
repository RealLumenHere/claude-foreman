"""wq#1146 phase 1: every decomposer guardrail, against a fake wq. Nothing here touches the
real queue, an LLM or the dispatcher."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import decomposer as dc  # noqa: E402
from decomposer import Sub  # noqa: E402

ARCH = {"standard-engineering", "mechanical-edit", "long-horizon-campaign"}
BODY = "Implement the thing concretely, with file paths and an acceptance test, in this repo."


def routes(a):
    return a == "long-horizon-campaign"


class FakeWQ:
    def __init__(self, existing=None, fail_add_at=None):
        self.existing = existing or []
        self.fail_add_at = fail_add_at
        self.added, self.notes, self.deps, self.closed = [], [], {}, []

    def children(self, pid):
        return self.existing

    def add(self, title, detail, lane, archetype, deps):
        if self.fail_add_at is not None and len(self.added) == self.fail_add_at:
            raise RuntimeError("wq add refused")
        nid = 5000 + len(self.added)
        self.added.append({"id": nid, "title": title, "detail": detail, "lane": lane,
                           "archetype": archetype, "deps": deps})
        return nid

    def set_deps(self, pid, ids):
        self.deps[pid] = list(ids)

    def note(self, tid, text):
        self.notes.append((tid, text))

    def done(self, tid, notes):
        self.closed.append((tid, notes))


def parent(detail="Build the whole campaign.", lane="build", pid=900):
    return {"id": str(pid), "lane": lane, "detail": detail, "title": "epic"}


def plan(n=3, **over):
    subs = [Sub(f"step {i}", BODY, archetype="standard-engineering") for i in range(n)]
    return [Sub(**{**s.__dict__, **over}) if over else s for s in subs]


def run(wq, subs, critic=lambda p, s: (True, ""), par=None, lanes=None):
    return dc.decompose(par or parent(), wq=wq, plan_fn=lambda p: subs, critic_fn=critic,
                        valid_archetypes=ARCH, routes_to_decompose=routes, lanes=lanes)


def test_happy_path_files_children_links_parent_and_marks_depth():
    wq = FakeWQ()
    res = run(wq, plan(3))
    assert res.status == "filed" and res.children == [5000, 5001, 5002]
    assert wq.deps[900] == [5000, 5001, 5002]
    for c in wq.added:
        assert f"{dc.MARK_FROM}900" in c["detail"] and f"{dc.MARK_DEPTH}1" in c["detail"]
        assert c["lane"] == "build"                       # defaults to the parent's lane
    assert any("DECOMPOSED into" in n for _, n in wq.notes)


def test_inter_sub_deps_become_real_ticket_ids():
    wq = FakeWQ()
    subs = plan(3)
    subs[2] = Sub("step 2", BODY, archetype="standard-engineering", deps=(0, 1))
    assert run(wq, subs).status == "filed"
    assert wq.added[2]["deps"] == [5000, 5001] and wq.added[0]["deps"] == []


@pytest.mark.parametrize("n", [0, 1, 9, 20])
def test_fan_out_cap_refuses_and_files_nothing(n):
    wq = FakeWQ()
    res = run(wq, plan(n)) if n else run(wq, [])
    assert res.status == "refused" and wq.added == [] and wq.deps == {}
    assert any("fan-out" in r for r in res.reasons)


def test_fan_out_bounds_are_inclusive():
    for n in (dc.MIN_SUBS, dc.MAX_SUBS):
        assert run(FakeWQ(), plan(n)).status == "filed"


def test_a_child_archetype_that_routes_to_decompose_is_refused():
    wq = FakeWQ()
    res = run(wq, plan(2, archetype="long-horizon-campaign"))
    assert res.status == "refused" and "recursion" in " ".join(res.reasons) and wq.added == []


def test_unknown_archetype_is_refused():
    assert run(FakeWQ(), plan(2, archetype="made-up")).status == "refused"


def test_a_decomposed_child_is_never_decomposed_again():
    wq = FakeWQ()
    child = parent(detail=f"Do a piece.\n\n{dc.MARK_FROM}900\n{dc.MARK_DEPTH}1\n", pid=5000)
    res = run(wq, plan(3), par=child)
    assert res.status == "refused" and "not decomposing again" in res.reasons[0]
    assert wq.added == []


def test_existing_children_make_it_idempotent_and_relink_the_parent():
    wq = FakeWQ(existing=[{"id": 7, "status": "open"}, {"id": 8, "status": "claimed"}])
    res = run(wq, plan(3))
    assert res.status == "already" and res.children == [7, 8]
    assert wq.added == [] and wq.deps[900] == [7, 8]


def test_parent_closes_when_every_child_is_done():
    wq = FakeWQ(existing=[{"id": 7, "status": "done"}, {"id": 8, "status": "done"}])
    res = run(wq, plan(3))
    assert res.status == "closed" and wq.added == []
    assert wq.closed and wq.closed[0][0] == 900


def test_one_open_child_keeps_the_parent_open():
    wq = FakeWQ(existing=[{"id": 7, "status": "done"}, {"id": 8, "status": "open"}])
    assert run(wq, plan(3)).status == "already" and wq.closed == []


def test_critic_rejection_files_nothing():
    wq = FakeWQ()
    res = run(wq, plan(3), critic=lambda p, s: (False, "sub 1 overlaps sub 2"))
    assert res.status == "refused" and "overlaps" in res.reasons[0] and wq.added == []


def test_a_critic_that_raises_is_a_refusal_not_a_pass():
    def boom(p, s):
        raise TimeoutError("gateway down")
    wq = FakeWQ()
    res = run(wq, plan(3), critic=boom)
    assert res.status == "refused" and "critic failed" in res.reasons[0] and wq.added == []


def test_a_planner_that_raises_files_nothing():
    def boom(p):
        raise ValueError("bad json")
    wq = FakeWQ()
    res = dc.decompose(parent(), wq=wq, plan_fn=boom, critic_fn=lambda p, s: (True, ""),
                       valid_archetypes=ARCH, routes_to_decompose=routes)
    assert res.status == "refused" and "planner failed" in res.reasons[0] and wq.added == []


@pytest.mark.parametrize("bad", [(1,), (2,), (5,), (-1,)])  # sub[1]: self, forward, out of range, negative
def test_deps_must_point_at_earlier_subs(bad):
    subs = plan(3)
    subs[1] = Sub("step 1", BODY, archetype="standard-engineering", deps=bad)
    res = run(FakeWQ(), subs)
    assert res.status == "refused" and any("earlier sub index" in r for r in res.reasons)


def test_duplicate_titles_and_thin_bodies_are_refused():
    subs = plan(3)
    subs[1] = Sub("step 0", BODY, archetype="standard-engineering")
    assert any("duplicate" in r for r in run(FakeWQ(), subs).reasons)
    thin = plan(3)
    thin[0] = Sub("step 0", "do it", archetype="standard-engineering")
    assert any("not a concrete task" in r for r in run(FakeWQ(), thin).reasons)


def test_a_lane_outside_the_allowed_set_is_refused():
    res = run(FakeWQ(), plan(2, lane="nowhere"), lanes={"build", "fleet"})
    assert res.status == "refused" and any("dispatchable lane" in r for r in res.reasons)


def test_partial_filing_failure_leaves_parent_unlinked_and_says_so():
    wq = FakeWQ(fail_add_at=2)
    res = run(wq, plan(4))
    assert res.status == "failed" and res.children == [5000, 5001]
    assert 900 not in wq.deps
    assert any("DECOMPOSE FAILED after filing 2/4" in n for _, n in wq.notes)


def test_parse_depth():
    assert dc.parse_depth("") == 0 and dc.parse_depth(None) == 0
    assert dc.parse_depth("x\nDecompose-depth: 1\n") == 1
    assert dc.parse_depth("Decompose-depth: 2") == 2
