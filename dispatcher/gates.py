#!/usr/bin/env python3
"""gates.py — the dispatcher's pool-independent gate pipeline as ordered Gate objects.

DISPATCH_REBUILD_SPEC step 8. `foreman_dispatcher._gate_candidate` used to be one 170-line
function of inline stages (DISPATCH_DISSECTION §2.2). Each stage is now a small class with
one method, `check(g)`, returning PASS or a `Fail`; `run()` walks GATES in order and stops at
the first Fail. The ORDER IS THE CONTRACT: it is DISSECTION §2.2 row for row, and a Fail is
logged as the same `skip` event (stage, reason, detail) the inline code emitted, one line per
decision (`test_every_decision_logged`).

A gate that delegates to a handler which already logs its own decision line and pages
(runaway, harness-defect hold, spawn loop, fire approval, capability) returns a Fail with
`logged=True` so the pipeline does not log it twice.

`Fail` carries what the step-11 parking table (gate_store.py) consumes:
  terminal            True when only a ticket rewrite / policy PR can change the verdict
  recheck_after       ISO timestamp when time alone can change the verdict (else None)
  fingerprint_inputs  the ticket columns whose edit re-opens the verdict
  parkable            opt-in: the verdict is a pure function of the ticket + config + the clock,
                      so `run()` may park the ticket and the scan skips it until the fingerprint
                      changes or recheck_after passes. Gates that read live external state
                      (runaway, harness-defect, spawn-loop, fire approval / no-probe, running units)
                      never park: their handlers page/dedup on their own and self-clear.

Gates reach the dispatcher's helpers through `g.fd` (the calling module, injected by
`_gate_candidate`) rather than importing it: run as a script the dispatcher is `__main__`,
and `import foreman_dispatcher` from here would load a second copy with its own globals.
"""
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Fail:
    stage: str
    reason: str
    detail: object = None          # omitted from the skip line when None
    terminal: bool = False
    recheck_after: object = None
    fingerprint_inputs: tuple = ()
    logged: bool = False           # the handler already wrote this decision's line
    parkable: bool = False


class _Pass:
    def __repr__(self):
        return "PASS"

    def __bool__(self):
        return True


PASS = _Pass()


class GateInput:
    """One candidate's working state, threaded through the gates (the inline stages' locals)."""

    def __init__(self, fd, ctx, cand):
        self.fd, self.ctx, self.cand = fd, ctx, cand
        self.tid, self.lane = cand["id"], cand["lane"]
        self.policy = ctx.cfg["lanes"][self.lane]
        self.fire_exempt = bool(self.policy.get("requires_approval"))
        self.repo = self.t = self.derived = self.model = None

    @property
    def log(self):
        return self.ctx.log


def _next_utc_midnight():
    now = datetime.now(timezone.utc)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return nxt.isoformat(timespec="seconds")


class Gate:
    """Protocol: `name` for tests/metrics, `check(g)` -> PASS | Fail."""
    name = ""

    def check(self, g):  # pragma: no cover — protocol
        raise NotImplementedError


class LaneCap(Gate):
    """stage 3 — lane policy (MUST 5): only `pilot`=3 is a real cap; the rest are 999."""
    name = "lane_cap"

    def check(self, g):
        if g.ctx.state["lane_counts"].get(g.lane, 0) >= g.policy["max_per_day"]:
            return Fail("lane_policy", "lane_cap", recheck_after=_next_utc_midnight(),
                        detail=f"max_per_day={g.policy['max_per_day']} reached",
                        parkable=True)
        return PASS


class HumanLane(Gate):
    """wq#1478: a human-owned lane is never worker-dispatchable. scan_candidates no longer
    emits these tickets, so this is the second wall for a caller that bypasses the scan; a
    Frank-gate whose premise is already met on live state is closed here, never dispatched."""
    name = "human_lane"

    def check(self, g):
        if g.lane not in g.fd.HUMAN_LANES:
            return PASS
        if g.fd._liveness_autoclosed(g.tid, None, g.log):
            return Fail("frank_gate", "frank_gated", terminal=True, logged=True)
        return Fail("frank_gate", "frank_gated", terminal=True,
                    detail="human-owned lane (Frank's action/decision queue) — "
                           "not worker-dispatchable")


class LaneRepo(Gate):
    """wq#713 repo mapping (MUST 7): a lane with no LANE_REPO row never guesses a repo.
    `auto` and any lane whose config policy declares `multi_repo: true` (wq#2242, simpill)
    resolve per ticket in RepoResolve below."""
    name = "lane_repo"

    def check(self, g):
        g.repo = g.fd.router.repo_for_lane(g.lane)
        multi_repo = g.lane == g.fd.REPO_HINT_LANE or g.policy.get("multi_repo") is True
        if not g.repo and not multi_repo:
            return Fail("lane_policy", "lane_unmapped", terminal=True,
                        detail="no LANE_REPO entry — never guess a repo",
                        fingerprint_inputs=("lane", "needs", "detail"), parkable=True)
        return PASS


class TicketLoad(Gate):
    """Read the ticket. wq#1408 §1.5: router.wq() bounds an sqlite-lock hang at 30 s, treated
    as "couldn't find the ticket". SystemExit is a vanished ticket unless the pass itself is
    being SIGTERMed (wq#2084)."""
    name = "ticket_load"

    def check(self, g):
        fd = g.fd
        try:
            g.t = fd.router.wq_show(g.tid)
        except SystemExit:
            if fd._terminating:
                raise
            return Fail("scan", "vanished")
        except subprocess.TimeoutExpired:
            return Fail("scan", "vanished", detail="wq show timed out")
        if g.t.get("status") != "open":
            return Fail("scan", "not_open", detail=g.t.get("status"))
        return PASS


class RepoMismatch(Gate):
    """wq#433: the TITLE names a different repo than the lane's (ISSUE-LOG #56 class)."""
    name = "repo_mismatch"

    def check(self, g):
        mismatch = g.fd.router.repo_mismatch(g.t)
        if mismatch:
            return Fail("lane_policy", "repo_mismatch", detail=mismatch, terminal=True,
                        fingerprint_inputs=("lane", "title"), parkable=True)
        return PASS


class RepoResolve(Gate):
    """wq#1094/#1123/#1331: the ticket's own repo hint (repo-sweep detail, needs:repo tag,
    verify-repo hint) wins over the lane default; for a plain lane ticket it returns the
    LANE_REPO path already in g.repo."""
    name = "repo_resolve"

    def check(self, g):
        g.repo = g.fd.router.repo_for_ticket(g.t) or g.repo
        if not g.repo:
            return Fail("lane_policy", "lane_unmapped", terminal=True,
                        detail="no LANE_REPO entry — never guess a repo",
                        fingerprint_inputs=("lane", "needs", "detail"), parkable=True)
        return PASS


class Capability(Gate):
    """stage 3.7 (wq#1437 derive / wq#1438 enforce). In front of the holds by design: a held
    ticket must still be linted and auto-tagged, because that is what lets the hold release."""
    name = "capability"

    def check(self, g):
        fd, ctx = g.fd, g.ctx
        ctx.lint_scanned += 1
        g.derived = fd.lint_capability(g.tid, g.t, g.lane, g.repo, g.log)
        if g.derived:
            ctx.lint_flagged += 1
        reason = fd.enforce_capability(ctx.state, g.tid, g.t, g.lane, g.repo, g.derived,
                                       ctx.dry_run, g.log)
        if reason:
            # only a denial / unresolved need is a fact about the ticket TEXT; an unreachable
            # path or unknown node is a fact about the fleet and can clear without an edit
            denied = fd.capability.REASON_CAPABILITY_DENIED_BY_POLICY
            hard = (denied, fd.capability.REASON_NEEDS_UNRESOLVED)
            # the secret wall (`denied`) is fail-closed and re-derived every pass, never
            # parked: a deny-list or sweep change must bite at once, not after a 6 h flush
            return Fail("capability", reason, terminal=reason in hard, logged=True,
                        fingerprint_inputs=("title", "detail", "needs", "archetype"),
                        parkable=reason != denied)
        return PASS


class Runaway(Gate):
    """stage 3.5 (wq#718): stuck below RUNAWAY_MAX_ATTEMPTS cycles without reaching done."""
    name = "runaway"

    def check(self, g):
        if g.tid not in g.ctx.stuck:
            return PASS
        g.fd.handle_runaway_stuck(g.ctx.state, g.tid, g.lane, g.ctx.stuck[g.tid]["attempts"],
                                  g.ctx.dry_run, g.log)
        return Fail("runaway", "runaway_stuck", terminal=True, logged=True,
                    fingerprint_inputs=("model", "archetype"))


class HarnessDefect(Gate):
    """stage 3.5b (wq#1415): the last failure.json is HARNESS_DEFECT and nothing has changed."""
    name = "harness_defect"

    def check(self, g):
        if g.tid not in g.ctx.harness_defects:
            return PASS
        g.fd.handle_harness_defect_hold(g.ctx.state, g.tid, g.lane,
                                        g.ctx.harness_defects[g.tid]["summary"],
                                        g.ctx.dry_run, g.log)
        return Fail("harness_defect", "harness_defect_hold", terminal=True, logged=True,
                    fingerprint_inputs=("model", "archetype", "needs"))


class SpawnLoop(Gate):
    """stage 3.5c (wq#1703): >=3 spawns in 3 h with no finished attempt."""
    name = "spawn_loop"

    def check(self, g):
        info = g.fd.spawn_loop_guard.looping(g.ctx.state, g.tid)
        if not info:
            return PASS
        g.fd.handle_spawn_loop(g.ctx.state, g.tid, g.lane, info, g.ctx.dry_run, g.log, t=g.t)
        return Fail("spawn_loop", "spawn_loop", logged=True)


class Nodispatch(Gate):
    """stage 3.6 (wq#428): an [EPIC] / [DO NOT CLAIM] rollup is never dispatchable work."""
    name = "nodispatch"

    def check(self, g):
        why = g.fd.nodispatch_reason(g.t)
        if why:
            return Fail("nodispatch", "nodispatch_marker", detail=why, terminal=True,
                        fingerprint_inputs=("title",), parkable=True)
        return PASS


class FrankGate(Gate):
    """stage 4 (MUST 3): a title marker, pending decision gate or unmet wq gate path. Never
    claimed, no override flag."""
    name = "frank_gate"

    def check(self, g):
        why = g.fd.frank_gate_reason(g.t)
        if not why:
            return PASS
        if g.fd._liveness_autoclosed(g.tid, g.t, g.log):  # wq#1478 §12.4, same as HumanLane
            return Fail("frank_gate", "frank_gated", logged=True)
        # marker / pending decision gate: only an edit (title, frank_response) changes them. An
        # unmet wq `gate` path is a file check the ticket never sees change — never parked.
        return Fail("frank_gate", "frank_gated", detail=why,
                    fingerprint_inputs=("title", "gate", "gate_type", "frank_response"),
                    parkable=not why.startswith("wq gate unmet"))


class FireApproval(Gate):
    """stage 5: requires_approval lanes need an approval file (MUST 4)."""
    name = "fire_approval"

    def check(self, g):
        if g.fire_exempt and not os.path.exists(g.fd.fire_approval_path(g.tid)):
            g.fd.handle_unapproved_fire(g.ctx.state, g.tid, g.lane, g.ctx.dry_run, g.log)
            return Fail("fire_approval", "fire_unapproved", logged=True)
        return PASS


class FireProbe(Gate):
    """stage 5b (wq#1261 §4 item 1): no probe, no dispatch. The legacy aq-row reference
    (wq#1234) still counts — only a ticket with NEITHER form is refused."""
    name = "fire_probe"

    def check(self, g):
        fd = g.fd
        if g.lane != fd.FIRE_LANE:
            return PASS
        detail = g.t.get("detail", "")
        if (fd.fire_gate.parse_fire_probe(detail) is None
                and fd.fire_gate.parse_probe_ref(detail)[1] is None):
            fd.handle_no_probe_fire(g.ctx.state, g.tid, g.lane, g.ctx.dry_run, g.log)
            # a fire safety refusal that pages on its own dedup ledger: re-derived every pass
            return Fail("fire_probe", "fire_no_probe", logged=True, fingerprint_inputs=("detail",))
        return PASS


class Hold(Gate):
    """stage 5c (wq#1461): a live `wq hold`, checked before deps so the hold is named even
    on a ticket that is still deps-blocked."""
    name = "hold"

    def check(self, g):
        why = g.fd.hold_reason(g.t)
        if why:
            expires = g.t.get("hold_expires")
            return Fail("hold", "held", detail=why, fingerprint_inputs=("hold_by", "hold_expires"),
                        recheck_after=(expires.replace(" ", "T") + "Z") if expires else None,
                        parkable=True)
        return PASS


class Deps(Gate):
    """stage 6 (MUST 2): any dep not `done`; a missing dep counts as unmet."""
    name = "deps"

    def check(self, g):
        unmet = g.fd.unmet_deps(g.t, g.ctx.dep_cache)
        if unmet:
            # gate_store adds each dep's status to the fingerprint, so a verdict drawn from a
            # failed `wq show` of a dep is not parked: the fingerprint would carry the dep's
            # real status and never change when the read recovers
            readable = all(g.ctx.dep_cache.get(d) != g.fd.DEP_UNREADABLE for d in unmet)
            return Fail("deps", "deps_unmet", detail=",".join(unmet), fingerprint_inputs=("deps",),
                        parkable=readable)
        return PASS


class AlreadyRunning(Gate):
    """stage 7 (MUST 6): live units are the ground truth, read once per pass."""
    name = "already_running"

    def check(self, g):
        if g.ctx.units is None:
            g.ctx.refresh_units()
        if f"fworker-wq{g.tid}" in g.ctx.units:
            return Fail("already_running", "already_running")
        return PASS


class Route(Gate):
    """the partition point: the router's model decides which pool's gates the ticket answers
    to. A ticket with no rung (or DECOMPOSE / an unroutable archetype) never spawns."""
    name = "route"

    def check(self, g):
        g.model, skip_reason = g.fd.candidate_model(g.t, g.tid)
        if not g.model:
            if skip_reason == g.fd.ROUTE_UNREADABLE:  # transient router failure: re-derive next pass
                return Fail("budget", skip_reason, detail="router.assign could not read the ticket")
            return Fail("budget", skip_reason, detail="router has no rung for this ticket",
                        terminal=True, fingerprint_inputs=("model", "effort", "archetype"),
                        parkable=True)
        return PASS


# DISPATCH_DISSECTION §2.2, evaluation order. Reordering changes which reason a ticket that
# trips two gates reports — the golden replay (test_gates_replay.py) is what holds it.
GATES = (LaneCap(), HumanLane(), LaneRepo(), TicketLoad(), RepoMismatch(), RepoResolve(),
         Capability(), Runaway(), HarnessDefect(), SpawnLoop(), Nodispatch(), FrankGate(),
         FireApproval(), FireProbe(), Hold(), Deps(), AlreadyRunning(), Route())


def _park(g, res):
    """Park a parkable Fail in gate_store; returns the row's recheck_after, or None when not
    parked (not parkable, dry-run, store off, or any store error — fail-open: an unparked
    ticket is simply evaluated again next pass)."""
    if not res.parkable or g.ctx.dry_run:
        return None
    store = g.fd.gate_store
    if not store.enabled():
        return None
    try:
        # from the g.t snapshot the gate judged, not a fresh read: an edit between the two must
        # leave the park fingerprint stale so the next pass re-evaluates (gates before
        # TicketLoad have no snapshot yet and read the row)
        fp = store.fingerprint(g.tid, res.fingerprint_inputs, getattr(g.ctx, "gate_salt", ""),
                               row=g.t)
        return store.park(g.tid, res.reason, fp, inputs=res.fingerprint_inputs, lane=g.lane,
                          terminal=res.terminal, recheck_after=res.recheck_after,
                          detail=res.detail)
    except Exception as e:  # noqa: BLE001 — parking is an optimisation, never a decision
        g.log.emit("error", stage="gate_store", ticket=g.tid, reason="park_failed",
                   detail=repr(e)[:300])
        return None


def run(g, gates=None):
    """Walk the gates in order. Returns PASS, or the first Fail — already logged as one
    `skip` event unless its handler logged it.

    A parked Fail is logged ONCE, when it is parked: the skip line itself carries
    `parked=True, recheck_after=...` (one line per decision stays true), or, for a handler that
    already wrote its own line, one `gate_parked` event follows it. The scan then never shows
    the ticket to the gates again until `gate_unparked`."""
    for gate in GATES if gates is None else gates:
        res = gate.check(g)
        if res is PASS:
            continue
        recheck = _park(g, res)
        if not res.logged:
            extra = {} if res.detail is None else {"detail": res.detail}
            if recheck:
                extra.update(parked=True, recheck_after=recheck)
            g.log.skip(g.tid, g.lane, res.stage, res.reason, **extra)
        elif recheck:
            g.log.emit("gate_parked", ticket=g.tid, lane=g.lane, reason=res.reason,
                       recheck_after=recheck)
        return res
    return PASS
