"""wq#2515: the LLM-facing half of the decomposer — contract parsing, critic gate,
routing row, wq adapter reads — plus the fake-LLM integration path: DECOMPOSE ticket
in, N<=cap sub-tickets out, parent linked. No gateway, no real queue."""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import decomposer as dc  # noqa: E402
import decompose_planner as dp  # noqa: E402

ARCH = {"standard-engineering", "mechanical-edit", "research-synthesis"}
LANES = {"build", "fleet", "research"}
BODY = ("Implement the thing concretely, with file paths and an acceptance test, "
        "in this repo, end to end.")

PLAN = f"""Here is the split you asked for.

SUBTICKET: 1
TITLE: build the parser
LANE: build
ARCHETYPE: standard-engineering
DEPS: none
DETAIL:
{BODY} Part one.

**SUBTICKET: 2**
TITLE: build the writer
LANE: build
ARCHETYPE: standard-engineering
DEPS: none
DETAIL:
{BODY} Part two.

SUBTICKET: 3
TITLE: wire and test both
LANE: fleet
ARCHETYPE: mechanical-edit
DEPS: 1, 2
DETAIL:
{BODY} Part three, after one and two.
"""


def parent(pid=900):
    return {"id": str(pid), "lane": "build", "title": "epic",
            "detail": "Build the whole campaign."}


def routes(a):
    return a not in ARCH


class FakeWQ:
    def __init__(self):
        self.added, self.deps, self.notes, self.closed = [], {}, [], []

    def children(self, pid):
        return []

    def add(self, title, detail, lane, archetype, deps):
        nid = 5000 + len(self.added)
        self.added.append({"id": nid, "title": title, "lane": lane,
                           "archetype": archetype, "deps": deps, "detail": detail})
        return nid

    def set_deps(self, pid, ids):
        self.deps[pid] = list(ids)

    def note(self, tid, text):
        self.notes.append((tid, text))

    def done(self, tid, notes):
        self.closed.append((tid, notes))


# ---- plan contract ---------------------------------------------------------------

def test_parse_plan_reads_blocks_titles_routes_and_deps():
    subs = dp.parse_plan(PLAN)
    assert [s.title for s in subs] == ["build the parser", "build the writer",
                                       "wire and test both"]
    assert subs[2].deps == (0, 1) and subs[2].archetype == "mechanical-edit"
    assert subs[1].lane == "build"          # markdown-decorated block still parses
    assert BODY in subs[0].detail


@pytest.mark.parametrize("broken", [
    PLAN.replace("ARCHETYPE: mechanical-edit\n", ""),        # missing contract field
    PLAN.replace("TITLE: build the writer\n", ""),           # missing title
    PLAN.replace("DEPS: 1, 2", "DEPS: 1, 9"),                # dep to unknown block
    PLAN.replace("DEPS: none\nDETAIL:\n" + BODY + " Part one.\n\n", "", 1),  # no DETAIL
    "I could not produce a plan, sorry.",                    # no blocks at all
])
def test_a_malformed_plan_is_rerun_never_partial(broken):
    with pytest.raises(dp.PlanRerun):
        dp.parse_plan(broken)


def test_a_malformed_plan_files_nothing_through_the_core():
    wq = FakeWQ()
    plan_fn = dp.make_plan_fn(ARCH, LANES, llm=lambda s, u: "no blocks here")
    res = dc.decompose(parent(), wq=wq, plan_fn=plan_fn,
                       critic_fn=lambda p, s: (True, ""),
                       valid_archetypes=ARCH, routes_to_decompose=routes, lanes=LANES)
    assert res.status == "refused" and wq.added == [] and wq.deps == {}


# ---- critic gate -----------------------------------------------------------------

def test_critic_parses_pass_fail_and_reason():
    ok, _ = dp.make_critic_fn(llm=lambda s, u: "prose...\nVERDICT: PASS\ncovers scope")(
        parent(), dp.parse_plan(PLAN))
    assert ok
    ok, why = dp.make_critic_fn(llm=lambda s, u: "VERDICT: FAIL\nsubs 1 and 2 overlap")(
        parent(), dp.parse_plan(PLAN))
    assert not ok and "overlap" in why


def test_a_critic_reply_without_a_verdict_fails_closed():
    wq = FakeWQ()
    res = dc.decompose(parent(), wq=wq,
                       plan_fn=dp.make_plan_fn(ARCH, LANES, llm=lambda s, u: PLAN),
                       critic_fn=dp.make_critic_fn(llm=lambda s, u: "looks fine to me"),
                       valid_archetypes=ARCH, routes_to_decompose=routes, lanes=LANES)
    assert res.status == "refused" and wq.added == []


# ---- integration: fake LLM end to end ---------------------------------------------

def fake_llm(system, user):
    return PLAN if "SUBTICKET: 1" in system else "VERDICT: PASS\ncovers the scope"


def test_decompose_ticket_in_capped_subs_out_parent_linked():
    wq = FakeWQ()
    res = dc.decompose(parent(), wq=wq,
                       plan_fn=dp.make_plan_fn(ARCH, LANES, llm=fake_llm),
                       critic_fn=dp.make_critic_fn(llm=fake_llm),
                       valid_archetypes=ARCH, routes_to_decompose=routes, lanes=LANES)
    assert res.status == "filed"
    assert dc.MIN_SUBS <= len(res.children) <= dc.MAX_SUBS
    assert wq.deps[900] == res.children                       # parent linked
    assert wq.added[2]["deps"] == [5000, 5001]                # inter-sub deps are real ids
    for c in wq.added:
        assert c["archetype"] in ARCH and f"{dc.MARK_FROM}900" in c["detail"]


def test_an_oversized_fake_llm_plan_is_rejected():
    blocks = "\n".join(
        f"SUBTICKET: {i}\nTITLE: step {i}\nLANE: build\n"
        f"ARCHETYPE: standard-engineering\nDEPS: none\nDETAIL:\n{BODY} step {i}.\n"
        for i in range(1, dc.MAX_SUBS + 2))
    wq = FakeWQ()
    res = dc.decompose(parent(), wq=wq,
                       plan_fn=dp.make_plan_fn(ARCH, LANES, llm=lambda s, u: blocks),
                       critic_fn=lambda p, s: (True, ""),
                       valid_archetypes=ARCH, routes_to_decompose=routes, lanes=LANES)
    assert res.status == "refused" and wq.added == []
    assert any("fan-out" in r for r in res.reasons)


# ---- routing row -----------------------------------------------------------------

def test_routing_row_is_opus5_at_medium_or_high_never_xhigh():
    model, effort = dp.routing_rung(os.path.join(dp.REPO_ROOT, "research", "models.yaml"))
    assert model == "claude-opus-5" and effort in ("medium", "high")


def test_routing_rung_clamps_xhigh_and_survives_a_missing_file(tmp_path):
    y = tmp_path / "models.yaml"
    y.write_text("routing:\n  decomposer: [opus-5@xhigh]\n")
    assert dp.routing_rung(str(y)) == ("claude-opus-5", "high")
    assert dp.routing_rung(str(tmp_path / "absent.yaml")) == dp.FALLBACK_RUNG


# ---- wq adapter reads ------------------------------------------------------------

def test_wqcli_children_matches_the_marker_exactly(tmp_path):
    db = tmp_path / "queue.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, lane TEXT, title TEXT,"
                  " detail TEXT, status TEXT, archetype TEXT)")
        c.executemany(
            "INSERT INTO tasks (id, lane, title, detail, status, archetype)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [(1, "build", "child a", f"do a\n\n{dc.MARK_FROM}900\n{dc.MARK_DEPTH}1\n",
              "open", "standard-engineering"),
             (2, "build", "child b", f"do b\n\n{dc.MARK_FROM}900\n{dc.MARK_DEPTH}1\n",
              "done", "standard-engineering"),
             (3, "build", "other", f"unrelated\n\n{dc.MARK_FROM}9001\n{dc.MARK_DEPTH}1\n",
              "open", "standard-engineering")])
    wq = dp.WqCli(db=str(db))
    assert wq.children(900) == [{"id": 1, "status": "open"}, {"id": 2, "status": "done"}]
    assert wq.children(9001) == [{"id": 3, "status": "open"}]
    assert wq.show(1)["title"] == "child a"
    with pytest.raises(KeyError):
        wq.show(99)
