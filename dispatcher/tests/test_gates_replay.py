"""DISPATCH_REBUILD step 8 (wq#2132): the gate objects reproduce the inline pipeline.

Three layers, all against gates.GATES through the dispatcher's own `_gate_candidate`:

  1. order       GATES is DISPATCH_DISSECTION §2.2, row for row (a frozen list).
  2. matrix      every pair of gate faults reports the EARLIER gate's reason, one `skip` line.
  3. golden      pass fdisp-20260920T141053Z-4161920 of dispatcher.log (120 tickets, the OLD
                 pipeline's decisions, fixtures/pass_*.jsonl) is replayed and every ticket must
                 get the same stage and reason; then the whole pass through `_do_pass` must
                 report dispatchable == len(ready) == 0 and dispatch_health must call it healthy.

The log holds decisions, not tickets, so each ticket is rebuilt from its golden reason (world
stubs at router/dispatcher seams). That tests the pipeline's ORDER and reason plumbing, which is
what this step rewrote. Stage 3.7's verdict is fed from the golden log rather than re-derived:
the capability lint's own behaviour, and the step-7 advisories that now dispatch tickets the
old lint held, are test_capability_replay.py's and the lint suite's to prove.
"""
import itertools
import json
import os
import sys
from datetime import datetime, timezone

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
from test_dispatcher import (  # noqa: E402,F401
    denv, fd, router, runaway, spawn_loop_guard)
import dispatch_health as dh  # noqa: E402
import gates  # noqa: E402

FIXTURE = os.path.join(TESTS_DIR, "fixtures", "pass_fdisp-20260920T141053Z-4161920.jsonl")
FIRE = fd.FIRE_LANE
# lanes the router has no LANE_REPO row for; `auto` is the one that resolves per ticket
UNMAPPED = {"nomap", "simpill", "auto"}

# (fault, the reason it produces, the lane it needs) in DISSECTION §2.2 evaluation order
FAULTS = [
    ("lane_cap", "lane_cap", None),
    ("human_lane", "frank_gated", "user"),
    ("lane_unmapped", "lane_unmapped", "nomap"),
    ("not_open", "not_open", None),
    ("repo_mismatch", "repo_mismatch", None),
    ("unresolved_repo", "lane_unmapped", "auto"),
    ("needs_unresolved", "needs_unresolved", None),
    ("runaway_stuck", "runaway_stuck", None),
    ("harness_defect_hold", "harness_defect_hold", None),
    ("spawn_loop", "spawn_loop", None),
    ("nodispatch_marker", "nodispatch_marker", None),
    ("frank_gated", "frank_gated", None),
    ("fire_unapproved", "fire_unapproved", FIRE),
    ("fire_no_probe", "fire_no_probe", FIRE),
    ("held", "held", None),
    ("deps_unmet", "deps_unmet", None),
    ("already_running", "already_running", None),
    ("route_unassigned", "route_unassigned", None),
    ("subsystem_held", "subsystem_held", None),
]
GATE_ORDER = ["lane_cap", "human_lane", "lane_repo", "ticket_load", "repo_mismatch",
              "repo_resolve", "capability", "runaway", "harness_defect", "spawn_loop",
              "nodispatch", "frank_gate", "fire_approval", "fire_probe", "hold", "deps",
              "already_running", "route", "subsystem"]
FIRE_PROBE = 'fire_probe: {"cmd": "true", "clear_when_exit": 0}'


class World:
    """Per-ticket stand-ins for what `wq show`, the router and the capability lint would say."""

    def __init__(self, monkeypatch, denv):
        self.denv = denv
        self.tickets, self.faults, self.cap = {}, {}, {}
        self.pings = []
        monkeypatch.setattr(router, "wq_show", self._show)
        monkeypatch.setattr(router, "repo_for_lane",
                            lambda lane: None if lane in UNMAPPED else f"/repo/{lane}")
        monkeypatch.setattr(router, "repo_for_ticket",
                            lambda t: None if t["lane"] in UNMAPPED else f"/repo/{t['lane']}")
        monkeypatch.setattr(router, "repo_mismatch", lambda t: t.get("_mismatch"))
        monkeypatch.setattr(fd, "lint_capability", lambda *a, **k: None)
        monkeypatch.setattr(fd, "enforce_capability", self._enforce)
        monkeypatch.setattr(fd, "candidate_model", self._model)
        monkeypatch.setattr(fd, "_liveness_autoclosed", lambda *a, **k: False)
        monkeypatch.setattr(gates, "_live_holds", lambda *a, **k: ({"fleet-ops": "other-worker"}, {}))
        monkeypatch.setattr(fd, "ntfy", lambda *a, **k: self.pings.append(a))
        # wq#2753: the replay world is hermetic except these two, which read the live
        # host's /proc/pressure — a loaded machine (e.g. a parallel pytest run) halted
        # the golden pass at cpu_pressure and failed the test spuriously.
        monkeypatch.setattr(fd.psi_pressure, "check", lambda cfg=None: ("ok", ""))
        monkeypatch.setattr(fd.cpu_pressure, "check", lambda cfg=None: ("ok", ""))

    def _show(self, tid):
        return self.tickets.get(int(tid)) or {"status": "open"}  # a dep: open, i.e. unmet

    def _enforce(self, state, tid, t, lane, repo, derived, dry_run, log):
        if "needs_unresolved" not in self.faults[tid]:
            return None
        reason, detail = self.cap.get(tid, ("needs_unresolved", "dir(/x) not under allow.read_dirs"))
        return fd.handle_capability_skip(state, tid, lane, reason, detail, dry_run, log)

    def _model(self, t, tid):
        if "route_unassigned" in self.faults[tid]:
            return None, "route_unassigned"
        return t.get("model") or "claude-sonnet-5", None

    def add(self, tid, lane, faults, mismatch=None, cap=None):
        f = self.faults[tid] = set(faults)
        if cap:
            self.cap[tid] = cap
        self.tickets[tid] = {
            "id": str(tid), "lane": lane, "status": "claimed" if "not_open" in f else "open",
            "title": ("[EPIC] roll-up" if "nodispatch_marker" in f else
                      "[FRANK GATE] decide" if "frank_gated" in f else "ordinary work"),
            "deps": "900001" if "deps_unmet" in f else "",
            "hold_by": "orch" if "held" in f else "None",
            "hold_expires": "2999-01-01 00:00:00" if "held" in f else "None",
            "subsystems": "fleet-ops" if "subsystem_held" in f else "",
            "detail": "" if "fire_no_probe" in f else FIRE_PROBE,
            "_mismatch": "title names another repo" if "repo_mismatch" in f else mismatch}
        if lane == FIRE and "fire_unapproved" not in f:
            open(fd.fire_approval_path(tid), "w").close()

    def ctx(self, cfg, tid, lane, log):
        f = self.faults[tid]
        try:
            os.unlink(fd.STATE_PATH)
        except FileNotFoundError:
            pass
        state = fd.load_state()
        if "lane_cap" in f:
            state["lane_counts"][lane] = cfg["lanes"][lane]["max_per_day"]
        if "spawn_loop" in f:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            state["spawn_ledger"][str(tid)] = [{"ts": now, "attempts": 0}] * 3
        ctx = fd._PassCtx(cfg, log, state, dry_run=False)
        ctx.units = [f"fworker-wq{tid}"] if "already_running" in f else []
        ctx.stuck = {tid: {"attempts": 5}} if "runaway_stuck" in f else {}
        ctx.harness_defects = {tid: {"summary": "held"}} if "harness_defect_hold" in f else {}
        return ctx


def _cfg(lanes, caps=None):
    caps = caps or {}
    return {"max_spawns_per_pass": 3, "cloud_ceiling": 3,
            "tight_mode": {"max_spawns_per_pass": 1, "min_pri": 1},
            "lanes": {lane: {"enabled": True, "max_per_day": caps.get(lane, 5),
                             **({"requires_approval": True} if lane == FIRE else {})}
                      for lane in lanes}}


@pytest.fixture
def world(denv, monkeypatch):
    return World(monkeypatch, denv)


def _decide(world, cfg, tid, lane):
    log = fd.DecisionLog(str(world.denv["tmp"] / "dispatcher.log"), "fdisp-replay")
    ctx = world.ctx(cfg, tid, lane, log)
    ready = fd._gate_candidate(ctx, {"id": tid, "lane": lane, "pri": 1, "epri": 1})
    skips = [e for e in log.events if e["event"] == "skip" and e["ticket"] == tid]
    return ready, skips


# ---- 1. order ---------------------------------------------------------------------------

def test_gates_are_the_dissection_order():
    assert [g.name for g in gates.GATES] == GATE_ORDER


def test_every_gate_implements_the_protocol():
    for g in gates.GATES:
        assert isinstance(g, gates.Gate) and callable(g.check) and g.name


# ---- 2. matrix --------------------------------------------------------------------------

def test_a_clean_ticket_passes_every_gate(world):
    world.add(1, "base", [])
    ready, skips = _decide(world, _cfg(["base"]), 1, "base")
    assert skips == [] and ready["model"] == "claude-sonnet-5" and ready["part"] == "cloud"
    assert ready["repo"] == "/repo/base"


@pytest.mark.parametrize("fault,reason,lane", FAULTS, ids=[f[0] for f in FAULTS])
def test_each_gate_alone_logs_exactly_one_skip(world, fault, reason, lane):
    lane = lane or "base"
    world.add(1, lane, [fault])
    ready, skips = _decide(world, _cfg([lane]), 1, lane)
    assert ready is None and [s["reason"] for s in skips] == [reason]


def test_every_pair_of_faults_reports_the_earlier_gate(world):
    checked = 0
    for (fa, ra, la), (fb, rb, lb) in itertools.combinations(FAULTS, 2):
        if la and lb and la != lb:
            continue  # two faults that need different lanes cannot co-occur
        lane = la or lb or "base"
        tid = 100 + checked
        world.add(tid, lane, [fa, fb])
        ready, skips = _decide(world, _cfg([lane]), tid, lane)
        assert ready is None and [s["reason"] for s in skips] == [ra], (fa, fb, skips)
        checked += 1
    assert checked > 100  # the matrix really ran


def test_a_handler_gate_is_logged_once_not_twice(world):
    """runaway/hold/loop/fire/capability handlers write their own line; the pipeline must not."""
    for i, fault in enumerate(("runaway_stuck", "harness_defect_hold", "spawn_loop",
                               "fire_unapproved", "fire_no_probe", "needs_unresolved")):
        lane = FIRE if fault.startswith("fire") else "base"
        world.add(10 + i, lane, [fault])
        _, skips = _decide(world, _cfg([lane]), 10 + i, lane)
        assert len(skips) == 1, (fault, skips)


class _GI:
    """Just enough GateInput for a gate that reads only lane_counts/policy."""

    def __init__(self, lane_counts, cap):
        class Ctx:
            state = {"lane_counts": lane_counts}
        self.ctx, self.lane, self.policy = Ctx(), "pilot", {"max_per_day": cap}


def test_fail_carries_the_parking_fields():
    """The step-11 parking table reads these: lane_cap is retryable at the next UTC midnight."""
    fail = gates.LaneCap().check(_GI({"pilot": 3}, cap=3))
    assert (fail.stage, fail.reason, fail.terminal) == ("lane_policy", "lane_cap", False)
    assert fail.recheck_after.endswith("T00:00:00+00:00")
    assert gates.LaneCap().check(_GI({"pilot": 2}, cap=3)) is gates.PASS


def test_a_failed_terminal_gate_is_marked_terminal(world):
    world.add(1, "base", ["nodispatch_marker"])
    cfg = _cfg(["base"])
    ctx = world.ctx(cfg, 1, "base", fd.DecisionLog(str(world.denv["tmp"] / "d.log"), "fdisp-x"))
    g = gates.GateInput(fd, ctx, {"id": 1, "lane": "base", "pri": 1, "epri": 1})
    fail = gates.run(g)
    assert fail.reason == "nodispatch_marker" and fail.terminal and fail.fingerprint_inputs == ("title",)


# ---- 3. golden replay -------------------------------------------------------------------

def _golden():
    with open(FIXTURE) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return [r for r in rows if r["event"] == "skip"], next(r for r in rows if r["event"] == "pass_end")


def _faults_for(row):
    reason, lane = row["reason"], row["lane"]
    if reason == "frank_gated":
        return ["human_lane"] if lane in fd.HUMAN_LANES else ["frank_gated"]
    if reason in ("capability_denied_by_policy", "needs_unresolved"):
        return ["needs_unresolved"]
    return [reason]  # lane_cap, runaway_stuck, spawn_loop, held, deps_unmet, ... are their own fault


def _build_world(world, skips):
    for row in skips:
        faults = _faults_for(row)
        mismatch = row["detail"] if row["reason"] == "repo_mismatch" else None
        cap = (row["reason"], row["detail"]) if row["stage"] == "capability" else None
        world.add(row["ticket"], row["lane"], [f for f in faults if f != "lane_unmapped"],
                  mismatch=mismatch, cap=cap)
    lanes = sorted({r["lane"] for r in skips})
    return _cfg(lanes, caps={"pilot": 3})


def test_fixture_is_the_old_pipelines_120_tickets():
    skips, end = _golden()
    assert len(skips) == 120 and len({r["ticket"] for r in skips}) == 120
    assert (end["candidates"], end["spawned"]) == (120, 0)


def test_golden_pass_every_ticket_gets_the_same_reason(world):
    skips, _ = _golden()
    cfg = _build_world(world, skips)
    got = {}
    for row in skips:
        ready, new = _decide(world, cfg, row["ticket"], row["lane"])
        assert ready is None, row
        assert len(new) == 1, (row, new)  # one line per decision
        got[row["ticket"]] = (new[0]["stage"], new[0]["reason"])
    want = {r["ticket"]: (r["stage"], r["reason"]) for r in skips}
    diff = {t: (want[t], got[t]) for t in want if want[t] != got[t]}
    assert diff == {}, diff


def test_golden_pass_end_dispatchable_is_len_ready_and_health_is_green(world, monkeypatch):
    skips, golden_end = _golden()
    cfg = _build_world(world, skips)
    monkeypatch.setattr(fd, "scan_candidates",
                        lambda c, log: [{"id": r["ticket"], "lane": r["lane"], "pri": 1, "epri": 1}
                                        for r in skips])
    monkeypatch.setattr(runaway, "stuck_ticket_ids", lambda: {
        t: {"attempts": 5} for t, f in world.faults.items() if "runaway_stuck" in f})
    monkeypatch.setattr(runaway, "harness_defect_ticket_ids", lambda seen=None: {
        t: {"summary": "held"} for t, f in world.faults.items() if "harness_defect_hold" in f})
    cfg.update(local_ceiling=4)
    log = fd.DecisionLog(str(world.denv["tmp"] / "dispatcher.log"), "fdisp-replay")
    state = fd.load_state()
    state["lane_counts"]["pilot"] = 3
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for t, f in world.faults.items():
        if "spawn_loop" in f:
            state["spawn_ledger"][str(t)] = [{"ts": now, "attempts": 0}] * 3

    end = fd._do_pass(cfg, log, state, dry_run=True)
    log.emit("pass_end", **end)

    ready = end["ready_local"] + end["ready_cloud"]
    assert (end["candidates"], ready, end["spawned"]) == (120, 0, 0)
    assert end["dispatchable"] == ready == 0
    assert (end["dispatchable_local"], end["dispatchable_cloud"]) == (0, 0)
    assert "dispatchable_unpooled" not in end
    assert dh.classify_passes(log.events)[0][2] == "healthy"
    # F7 regression: the OLD pass_end (dispatchable=1, dispatchable_unpooled=1 from the one
    # harness_defect_hold ticket) must no longer page either
    golden_end = dict(golden_end, pass_id="fdisp-golden", ts=now)
    assert golden_end["dispatchable_unpooled"] == 1
    assert dh.classify_passes([golden_end])[0][2] == "healthy"
