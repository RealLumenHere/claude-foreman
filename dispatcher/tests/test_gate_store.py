"""DISPATCH_REBUILD step 11 (wq#2134): gate parking store + dispatch janitor.

Proof obligations from the ticket, each a test below:
  - per-pass evaluated candidates equals the unparked set           test_pass_evaluates_open_minus_parked
  - one digest row per cause per day                                test_digest_one_row_per_cause_per_day
  - editing a parked ticket changes its fingerprint and it is
    re-evaluated on the next pass                                   test_editing_a_parked_ticket_is_reevaluated
  - sqlite failure = fail-open                                      test_sqlite_failure_is_fail_open

The real `wq` runs against the scratch WQ_DB `denv` builds (same harness as test_dispatcher.py);
the fake lumen-run / governor shims mean nothing touches quota or a live service.
"""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_dispatcher import (  # noqa: E402,F401
    denv, add_ticket, log_events, db_exec, add_needs_column, write_config, _script,
    _lint_roster, _apply_dir, fd, router, self_heal, spawn_loop_guard, runaway, WQ_BIN)
import dispatch_janitor as janitor  # noqa: E402
import gate_store  # noqa: E402


def wq(*args):
    p = subprocess.run([WQ_BIN, *args], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout


def column(denv, tid, col):
    con = sqlite3.connect(denv["wq_db"])
    try:
        return con.execute(f"SELECT {col} FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    finally:
        con.close()


def passes(denv):
    """The dispatcher log split into one event list per pass (pass ids can collide within a
    second under test, so split on pass_start)."""
    out = []
    for e in log_events(denv):
        if e["event"] == "pass_start":
            out.append([])
        if out:
            out[-1].append(e)
    return out


def pass_end(events):
    return [e for e in events if e["event"] == "pass_end"][0]


def park(tid, reason="frank_gated", **kw):
    kw.setdefault("terminal", True)
    return gate_store.park(tid, reason, gate_store.fingerprint(tid, ("title",)), **kw)


# ---- gate_store unit --------------------------------------------------------------------

def test_park_is_parked_unpark_roundtrip(denv):
    tid = add_ticket("[FRANK GATE] hold")
    fp = gate_store.fingerprint(tid, ("title",))
    assert not gate_store.is_parked(tid, fp)
    assert gate_store.park(tid, "frank_gated", fp, inputs=("title",), lane="testlane", terminal=True)
    assert gate_store.is_parked(tid, fp)
    assert not gate_store.is_parked(tid, "some-other-fingerprint")
    assert gate_store.by_reason() == {"frank_gated": 1}
    assert gate_store.unpark(tid) and not gate_store.is_parked(tid, fp)
    assert gate_store.by_reason() == {}


def test_recheck_rules(denv):
    tid = add_ticket("[FRANK GATE] hold")
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    fp = gate_store.fingerprint(tid, ("title",))
    # terminal: 6 h safety flush; anything else: 1 h
    assert gate_store.park(tid, "frank_gated", fp, terminal=True, now=now) == "2026-09-20 18:00:00"
    assert gate_store.park(tid, "deps_unmet", fp, now=now) == "2026-09-20 13:00:00"
    # natural rechecks win: lane_cap = next UTC midnight, held = the hold's expiry
    assert gate_store.park(tid, "lane_cap", fp, recheck_after="2026-09-21T00:00:00+00:00",
                           now=now) == "2026-09-21 00:00:00"
    assert gate_store.park(tid, "held", fp, recheck_after="2026-09-20T15:30:00Z",
                           now=now) == "2026-09-20 15:30:00"
    # due() lists the row once its recheck passes, and is_parked() stops holding it
    assert gate_store.due(now=now) == []
    assert [r["ticket"] for r in gate_store.due(now=now + timedelta(hours=4))] == [tid]
    assert gate_store.is_parked(tid, fp, now=now)
    assert not gate_store.is_parked(tid, fp, now=now + timedelta(hours=4))


def test_lane_cap_parks_until_utc_midnight_and_a_config_edit_reopens_it(denv):
    write_config(denv, lanes={"testlane": {"enabled": True, "max_per_day": 1}})
    first, capped = add_ticket("first under the cap"), add_ticket("second over the cap")
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [first]
    assert gate_store.parked_rows() == {}   # capped at ADMIT (first spawned mid-pass): not a gate fail

    assert fd.run_pass() == 0               # next pass the gate itself sees the cap and parks
    row = gate_store.parked_rows()[capped]
    assert row["reason"] == "lane_cap"
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0) + timedelta(days=1)
    assert row["recheck_after"] == midnight.strftime("%Y-%m-%d %H:%M:%S")

    assert fd.run_pass() == 0
    assert pass_end(passes(denv)[2])["evaluated"] == 0
    # the cap is policy: a committed config change is part of every fingerprint
    write_config(denv, lanes={"testlane": {"enabled": True, "max_per_day": 5}})
    assert fd.run_pass() == 0
    p4 = passes(denv)[3]
    assert [e["ticket"] for e in p4 if e["event"] == "spawn"] == [capped]


def test_by_reason_counts_only_open_tickets(denv):
    a, b = add_ticket("[FRANK GATE] a"), add_ticket("[FRANK GATE] b")
    park(a), park(b)
    assert gate_store.by_reason() == {"frank_gated": 2}
    db_exec(denv, "UPDATE tasks SET status='done' WHERE id=?", (b,))
    assert gate_store.by_reason() == {"frank_gated": 1}
    assert gate_store.purge_closed() == 1 and set(gate_store.parked_rows()) == {a}


def test_lever_off_never_parks(denv, monkeypatch):
    monkeypatch.setenv("FOREMAN_GATE_STORE", "off")
    tid = add_ticket("[FRANK GATE] hold")
    fp = gate_store.fingerprint(tid, ("title",))
    assert gate_store.park(tid, "frank_gated", fp) is None
    assert not gate_store.is_parked(tid, fp) and gate_store.parked_rows() == {}


def test_sqlite_failure_is_fail_open_at_the_api(tmp_path, monkeypatch):
    """A db that cannot be opened reads as 'nothing parked' from every entry point — no
    exception, never a hidden ticket."""
    monkeypatch.setenv("WQ_DB", str(tmp_path))  # a directory: sqlite cannot open it
    cands = [{"id": 1, "lane": "testlane"}]
    assert gate_store.fingerprint(1, ("title",)) is None
    assert gate_store.park(1, "frank_gated", "fp") is None
    assert gate_store.is_parked(1, "fp") is False
    assert gate_store.partition(cands) == (cands, [], [])
    assert gate_store.due() == [] and gate_store.by_reason() == {} and gate_store.unpark(1) is False


# ---- the pass ---------------------------------------------------------------------------

def test_pass_evaluates_open_minus_parked(denv):
    gated = [add_ticket(f"[FRANK GATE] g{i}") for i in range(3)]
    epic = add_ticket("[EPIC] rollup")
    assert fd.run_pass() == 0
    end1 = pass_end(passes(denv)[0])
    assert (end1["candidates"], end1["evaluated"], end1["parked"]) == (4, 4, 0)
    assert gate_store.by_reason() == {"frank_gated": 3, "nodispatch_marker": 1}
    # each park logged once: the skip line itself carries the flag
    for tid in gated + [epic]:
        evs = log_events(denv, ticket=tid)
        assert len(evs) == 1 and evs[0]["parked"] is True and evs[0]["recheck_after"]

    fresh = add_ticket("clean ticket")
    assert fd.run_pass() == 0
    p2 = passes(denv)[1]
    end2 = pass_end(p2)
    assert (end2["candidates"], end2["evaluated"], end2["parked"]) == (5, 1, 4)
    # decisions this pass exist for the unparked set and nobody else
    assert {e["ticket"] for e in p2 if "ticket" in e} == {fresh}
    assert [e["ticket"] for e in p2 if e["event"] == "spawn"] == [fresh]

    # a third pass: the spawned ticket is still `open` under the fake lumen-run (its worker
    # unit is live, so it is re-evaluated: already_running); the four parked ones log nothing
    assert fd.run_pass() == 0
    p3 = passes(denv)[2]
    end3 = pass_end(p3)
    assert (end3["candidates"], end3["evaluated"], end3["parked"]) == (5, 1, 4)
    assert {e["ticket"] for e in p3 if "ticket" in e} == {fresh}


def test_editing_a_parked_ticket_is_reevaluated(denv):
    tid = add_ticket("[FRANK GATE] wait for Frank")
    fp0 = gate_store.fingerprint(tid, ("title",))
    assert fd.run_pass() == 0 and gate_store.by_reason() == {"frank_gated": 1}

    wq("edit", str(tid), "--pri", "3")  # not a fingerprint input: stays parked
    assert fd.run_pass() == 0
    assert (pass_end(passes(denv)[1])["evaluated"], pass_end(passes(denv)[1])["parked"]) == (0, 1)

    wq("edit", str(tid), "--title", "rewritten without the marker")
    assert gate_store.fingerprint(tid, ("title",)) != fp0
    assert fd.run_pass() == 0
    p3 = passes(denv)[2]
    assert (pass_end(p3)["evaluated"], pass_end(p3)["parked"]) == (1, 0)
    unparked = [e for e in p3 if e["event"] == "gate_unparked"]
    assert len(unparked) == 1 and unparked[0]["why"] == "fingerprint_changed"
    assert [e["ticket"] for e in p3 if e["event"] == "spawn"] == [tid]
    assert gate_store.parked_rows() == {}


def test_edit_that_still_fails_reparks_under_the_new_fingerprint(denv):
    tid = add_ticket("[FRANK GATE] wait for Frank")
    assert fd.run_pass() == 0
    wq("edit", str(tid), "--title", "[FRANK GATE] wait for Frank, reworded")
    assert fd.run_pass() == 0
    p2 = passes(denv)[1]
    assert [e["why"] for e in p2 if e["event"] == "gate_unparked"] == ["fingerprint_changed"]
    skips = [e for e in p2 if e["event"] == "skip"]
    assert len(skips) == 1 and skips[0]["parked"] is True
    assert fd.run_pass() == 0 and pass_end(passes(denv)[2])["evaluated"] == 0


def test_dep_done_unparks_the_dependent(denv):
    dep = add_ticket("[FRANK GATE] the dep stays open")
    child = add_ticket("needs the dep", deps=str(dep))
    assert fd.run_pass() == 0
    assert gate_store.by_reason() == {"frank_gated": 1, "deps_unmet": 1}
    assert fd.run_pass() == 0 and pass_end(passes(denv)[1])["evaluated"] == 0

    db_exec(denv, "UPDATE tasks SET status='done' WHERE id=?", (dep,))
    assert fd.run_pass() == 0
    p3 = passes(denv)[2]
    unparked = [e for e in p3 if e["event"] == "gate_unparked"]
    assert [(e["ticket"], e["why"]) for e in unparked] == [(child, "deps_changed")]
    assert [e["ticket"] for e in p3 if e["event"] == "spawn"] == [child]


def test_recheck_due_unparks_by_time(denv):
    tid = add_ticket("[FRANK GATE] hold")
    assert fd.run_pass() == 0
    con = sqlite3.connect(denv["wq_db"])
    con.execute("UPDATE dispatch_gate SET recheck_after='2000-01-01 00:00:00'")
    con.commit()
    con.close()
    assert fd.run_pass() == 0
    p2 = passes(denv)[1]
    assert [(e["ticket"], e["why"]) for e in p2 if e["event"] == "gate_unparked"] == \
        [(tid, "recheck_due")]


def test_gates_that_read_live_state_are_never_parked(denv):
    """The unmet-file wq gate can clear without an edit, and the secret wall is fail-closed and
    re-derived every pass: neither may ever sit in the store."""
    add_ticket("gated on a missing file", gate=str(denv["tmp"] / "no-such-file"))
    assert fd.run_pass() == 0 and fd.run_pass() == 0
    assert gate_store.parked_rows() == {}
    assert pass_end(passes(denv)[1])["evaluated"] == 1


def test_secret_denied_ticket_is_never_parked(denv, monkeypatch):
    _lint_roster(monkeypatch)
    _apply_dir(monkeypatch)
    tid = add_ticket("ops: rotate the token", detail="read /home/lumen/.secrets/x.token first")
    add_needs_column(denv)
    assert fd.run_pass() == 0 and fd.run_pass() == 0
    assert gate_store.parked_rows() == {}
    assert len([e for e in log_events(denv, "skip", ticket=tid)]) == 2


def test_lever_off_restores_evaluate_everything(denv, monkeypatch):
    monkeypatch.setenv("FOREMAN_GATE_STORE", "off")
    add_ticket("[FRANK GATE] a"), add_ticket("[EPIC] b")
    assert fd.run_pass() == 0 and fd.run_pass() == 0
    for p in passes(denv):
        assert (pass_end(p)["candidates"], pass_end(p)["evaluated"], pass_end(p)["parked"]) == (2, 2, 0)
        assert not [e for e in p if e.get("parked")]
    assert gate_store.parked_rows() == {}


def test_sqlite_failure_is_fail_open(denv, monkeypatch):
    tid = add_ticket("[FRANK GATE] hold")
    assert fd.run_pass() == 0 and gate_store.by_reason() == {"frank_gated": 1}

    def locked(write):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(gate_store, "_connect", locked)
    assert fd.run_pass() == 0                      # the pass survives a dead store ...
    p2 = passes(denv)[1]
    assert (pass_end(p2)["evaluated"], pass_end(p2)["parked"]) == (1, 0)   # ... and evaluates it
    skips = [e for e in p2 if e["event"] == "skip" and e["ticket"] == tid]
    assert len(skips) == 1 and "parked" not in skips[0]
    assert not [e for e in p2 if e["event"] == "error"]


def test_dry_run_reads_the_store_but_writes_nothing(denv):
    add_ticket("[FRANK GATE] hold")
    assert fd.run_pass(dry_run=True) == 0
    assert gate_store.parked_rows() == {}
    assert fd.run_pass() == 0 and len(gate_store.parked_rows()) == 1
    con = sqlite3.connect(denv["wq_db"])
    con.execute("UPDATE dispatch_gate SET recheck_after='2000-01-01 00:00:00'")
    con.commit()
    con.close()
    assert fd.run_pass(dry_run=True) == 0          # would unpark, but deletes nothing
    assert len(gate_store.parked_rows()) == 1


# ---- the janitor ------------------------------------------------------------------------

@pytest.fixture
def jan(denv, monkeypatch):
    monkeypatch.setattr(janitor, "LOG_PATH", str(denv["tmp"] / "janitor.log"))
    monkeypatch.setattr(janitor, "STATE_PATH", str(denv["tmp"] / "jstate.json"))
    monkeypatch.setattr(spawn_loop_guard, "STATE_PATH", str(denv["tmp"] / "dstate.json"))
    monkeypatch.setattr(self_heal, "DISARM_PATH", str(denv["tmp"] / "DISARM"))
    monkeypatch.setattr(self_heal, "STOP_PATH", str(denv["tmp"] / "STOP"))
    monkeypatch.setattr(janitor, "live_slots", lambda backends=None: 0)
    return self_heal.DecisionLog(str(denv["tmp"] / "janitor.log"), "test")


def block(tid, reason):
    wq("block", str(tid), reason)


def test_release_is_batched_at_live_slots_times_two(denv, jan, monkeypatch):
    tids = [add_ticket(f"rung casualty {i}") for i in range(5)]
    for tid in tids:
        block(tid, "RUNG_FAULT: hang on hathor")
    monkeypatch.setattr(self_heal, "rung_backend_healthy",
                        lambda t, tid: (True, "hathor-llama8b", "healthy"))
    monkeypatch.setattr(janitor, "live_slots", lambda backends=None: 1)
    assert janitor.release_rung_faults(jan, False) == (2, 3)
    assert [column(denv, t, "status") for t in tids] == ["open", "open", "blocked", "blocked", "blocked"]
    # nothing live -> nothing released, whatever is blocked
    monkeypatch.setattr(janitor, "live_slots", lambda backends=None: 0)
    assert janitor.release_rung_faults(jan, False) == (0, 3)


def test_release_waits_for_a_healthy_backend_and_skips_other_classes(denv, jan, monkeypatch):
    rung, work = add_ticket("rung casualty"), add_ticket("real work block")
    block(rung, "RUNG_FAULT: hang")
    block(work, "WORK_BLOCKED: needs a decision")
    monkeypatch.setattr(janitor, "live_slots", lambda backends=None: 4)
    monkeypatch.setattr(self_heal, "rung_backend_healthy",
                        lambda t, tid: (False, "hathor-llama8b", "backend_unhealthy"))
    assert janitor.release_rung_faults(jan, False) == (0, 0)
    monkeypatch.setattr(self_heal, "rung_backend_healthy",
                        lambda t, tid: (True, "hathor-llama8b", "healthy"))
    assert janitor.release_rung_faults(jan, False) == (1, 0)
    assert column(denv, rung, "status") == "open" and column(denv, work, "status") == "blocked"


def test_release_never_touches_a_secret_denied_ticket(denv, jan, monkeypatch):
    _lint_roster(monkeypatch)
    _apply_dir(monkeypatch)
    tid = add_ticket("ops: rotate the token", detail="read /home/lumen/.secrets/x.token first")
    block(tid, "RUNG_FAULT: hang")
    monkeypatch.setattr(janitor, "live_slots", lambda backends=None: 4)
    monkeypatch.setattr(self_heal, "rung_backend_healthy",
                        lambda t, tid: (True, "hathor-llama8b", "healthy"))
    assert janitor.release_rung_faults(jan, False) == (0, 0)
    assert column(denv, tid, "status") == "blocked"


CFG = {"lanes": {"nestlane": {"enabled": True}, "testlane": {"enabled": True}}}


def test_relane_on_an_unambiguous_title_marker(denv, jan, monkeypatch):
    monkeypatch.setitem(router.LANE_REPO, "nestlane", "/tmp/wq2134/nest")
    monkeypatch.setitem(router.TITLE_REPO_MARKERS, "nestlane", "/tmp/wq2134/nest")
    tid = add_ticket("[nestlane] port the thing")
    plain = add_ticket("no marker here")
    park(tid, "repo_mismatch"), park(plain, "repo_mismatch")
    assert janitor.relane_marked(jan, False, CFG) == 1
    assert column(denv, tid, "lane") == "nestlane" and column(denv, plain, "lane") == "testlane"
    assert set(gate_store.parked_rows()) == {plain}   # relaned ticket unparked; the other stays
    assert [e["event"] for e in map(json.loads, open(jan.path))] == ["janitor_relane"]


def test_relane_refuses_an_ambiguous_marker(denv, jan, monkeypatch):
    for lane in ("nestlane", "nestlane2"):
        monkeypatch.setitem(router.LANE_REPO, lane, "/tmp/wq2134/nest")
    monkeypatch.setitem(router.TITLE_REPO_MARKERS, "nestlane", "/tmp/wq2134/nest")
    cfg = {"lanes": {"nestlane": {"enabled": True}, "nestlane2": {"enabled": True}}}
    tid = add_ticket("[nestlane] port the thing")
    park(tid, "repo_mismatch")
    assert janitor.relane_marked(jan, False, cfg) == 0
    assert column(denv, tid, "lane") == "testlane" and set(gate_store.parked_rows()) == {tid}


def test_relane_never_touches_a_secret_denied_ticket(denv, jan, monkeypatch):
    _lint_roster(monkeypatch)
    _apply_dir(monkeypatch)
    monkeypatch.setitem(router.LANE_REPO, "nestlane", "/tmp/wq2134/nest")
    monkeypatch.setitem(router.TITLE_REPO_MARKERS, "nestlane", "/tmp/wq2134/nest")
    tid = add_ticket("[nestlane] rotate", detail="read /home/lumen/.secrets/x.token first")
    park(tid, "repo_mismatch")
    assert janitor.relane_marked(jan, False, CFG) == 0
    assert column(denv, tid, "lane") == "testlane"


def _spawn_loop_state(denv, tid, trips):
    now = datetime.now(timezone.utc)
    ts = lambda h: (now - timedelta(hours=h)).isoformat(timespec="seconds")  # noqa: E731
    state = {"spawn_ledger": {str(tid): [{"ts": ts(1.0), "attempts": 0}, {"ts": ts(0.9), "attempts": 0},
                                         {"ts": ts(0.8), "attempts": 0}]},
             "spawn_loop_trips": {str(tid): [ts(h) for h in trips]}}
    (denv["tmp"] / "dstate.json").write_text(json.dumps(state))


def test_spawn_loop_second_trip_blocks(denv, jan):
    first, second = add_ticket("first trip"), add_ticket("second trip")
    _spawn_loop_state(denv, first, trips=[])
    assert janitor.block_spawn_loops(jan, False) == 0 and column(denv, first, "status") == "open"
    _spawn_loop_state(denv, second, trips=[10])
    assert janitor.block_spawn_loops(jan, False) == 1
    assert column(denv, second, "status") == "blocked"
    assert column(denv, second, "block_reason").startswith("SPAWN_LOOP:")


def test_stale_harness_hold_is_cleared_but_a_live_one_is_kept(denv, jan):
    edited, held = add_ticket("edited since failure"), add_ticket("still the same")
    seen = {}
    for tid in (edited, held):
        d = denv["runs_root"] / f"wq{tid}"
        d.mkdir()
        f = d / "failure.json"
        f.write_text(json.dumps({"triage": "HARNESS_DEFECT", "summary": "boom"}))
        seen[str(tid)] = {"fp": runaway.edit_fingerprint(self_heal.wq_show(tid)),
                          "mtime_ns": os.stat(f).st_mtime_ns}
    seen[str(edited)]["fp"] = "model-before-the-edit|x|y"
    (denv["tmp"] / "dstate.json").write_text(json.dumps({"harness_defect_seen": seen}))
    assert janitor.clear_stale_harness_holds(jan, False) == 1
    assert (denv["runs_root"] / f"wq{edited}" / "failure.json.cleared").exists()
    assert not (denv["runs_root"] / f"wq{edited}" / "failure.json").exists()
    assert (denv["runs_root"] / f"wq{held}" / "failure.json").exists()


def test_digest_one_row_per_cause_per_day(denv, jan):
    log = denv["tmp"] / "jan-aq.log"
    aq = _script(denv["bin_dir"] / "jan-aq", f"#!/usr/bin/env bash\nprintf '%s\\n' \"$3\" >> {log}\n")
    a, b = add_ticket("[nestlane] a"), add_ticket("[nestlane] b")
    c, d, e = add_ticket("c"), add_ticket("d"), add_ticket("e")
    park(a, "repo_mismatch"), park(b, "repo_mismatch"), park(c, "route_unassigned")
    park(d, "lane_cap"), park(e, "held")   # self-resolving causes: never digested
    state = {"digest": {}}
    day1 = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)
    assert janitor.post_digests(jan, False, state, now=day1, aq_bin=aq) == \
        ["repo_mismatch", "route_unassigned"]
    assert janitor.post_digests(jan, False, state, now=day1 + timedelta(hours=8), aq_bin=aq) == []
    assert log.read_text().split() == ["parked-repo_mismatch-2026-09-20",
                                       "parked-route_unassigned-2026-09-20"]
    day2 = day1 + timedelta(days=1)
    assert janitor.post_digests(jan, False, state, now=day2, aq_bin=aq) == \
        ["repo_mismatch", "route_unassigned"]
    assert len(log.read_text().split()) == 4
    body = janitor.digest_body("repo_mismatch", gate_store.rows_by_reason()["repo_mismatch"])
    assert f"wq#{a}" in body and f"wq#{b}" in body and "2 open ticket(s)" in body


def test_digest_is_retried_when_aq_fails(denv, jan):
    park(add_ticket("[nestlane] a"), "repo_mismatch")
    state = {"digest": {}}
    assert janitor.post_digests(jan, False, state, aq_bin="/nonexistent/aq") == []
    assert state["digest"] == {}        # not marked sent: the next run tries again


def test_run_pass_end_to_end_files_each_digest_once(denv, jan, monkeypatch):
    log = denv["tmp"] / "jan-aq.log"
    monkeypatch.setattr(janitor, "AQ_BIN", _script(
        denv["bin_dir"] / "jan-aq", f"#!/usr/bin/env bash\nprintf '%s\\n' \"$3\" >> {log}\n"))
    park(add_ticket("[FRANK GATE] a"), "frank_gated")
    done = add_ticket("closed since")
    park(done, "nodispatch_marker")
    db_exec(denv, "UPDATE tasks SET status='done' WHERE id=?", (done,))
    assert janitor.run_pass() == 0 and janitor.run_pass() == 0
    assert len(log.read_text().split()) == 1                  # one cause open, filed once
    assert set(gate_store.parked_rows()) != {done}            # the closed ticket's row was purged
    ends = [json.loads(l) for l in open(jan.path) if '"pass_end"' in l]
    assert ends[0]["purged"] == 1 and ends[0]["digests"] == ["frank_gated"]


def test_capability_rows_close_when_the_denied_ticket_moves_on(denv, jan, monkeypatch):
    """wq#3457 (f): a capability-denied row closes once wq says the ticket is not blocked."""
    import capability_proposal
    moved, still = add_ticket("was denied"), add_ticket("still denied")
    block(still, "CAPABILITY_DENIED: dir(/x)")
    listing = denv["tmp"] / "aq-list.txt"
    listing.write_text("".join(
        f"[{50 + i}] open     x2   foreman-capability capability-denied-wq{t}   "
        f"owner=unassigned   2026-10-01T00:00:00Z  wq#{t} (P1) blocked CAPABILITY_DENIED\n"
        for i, t in enumerate((moved, still))))
    log = denv["tmp"] / "cap-aq.log"
    monkeypatch.setattr(capability_proposal, "AQ_BIN", _script(
        denv["bin_dir"] / "cap-aq", f'#!/usr/bin/env bash\nif [ "$1" = list ]; then cat {listing}; '
        f'exit 0; fi\nprintf "%s\\n" "$*" >> {log}\n'))
    assert janitor.reconcile_capability_rows(jan, dry_run=True) == 0 and not log.exists()
    assert janitor.reconcile_capability_rows(jan, dry_run=False) == 1
    assert log.read_text().splitlines() == [
        f"resolve 50 wq#3457 reconcile: wq#{moved} is open, no longer blocked"]


def test_dry_run_and_kill_switch_write_nothing(denv, jan, monkeypatch):
    tids = [add_ticket("rung casualty"), add_ticket("[nestlane] port")]
    block(tids[0], "RUNG_FAULT: hang")
    park(tids[1], "repo_mismatch")
    monkeypatch.setattr(janitor, "live_slots", lambda backends=None: 4)
    monkeypatch.setattr(self_heal, "rung_backend_healthy",
                        lambda t, tid: (True, "hathor-llama8b", "healthy"))
    monkeypatch.setattr(janitor, "AQ_BIN", "/nonexistent/aq")
    assert janitor.run_pass(dry_run=True) == 0
    assert column(denv, tids[0], "status") == "blocked" and set(gate_store.parked_rows()) == {tids[1]}
    assert not os.path.exists(janitor.STATE_PATH)
    (denv["tmp"] / "DISARM").write_text("")
    assert janitor.run_pass() == 0
    assert column(denv, tids[0], "status") == "blocked"


# ---- dashboards -------------------------------------------------------------------------

def test_dashboard_shows_parked_by_reason_and_per_backend_slots(denv, monkeypatch):
    import dashboard
    import dashboard_data as dd
    import local_capacity
    park(add_ticket("[FRANK GATE] a"), "frank_gated")
    park(add_ticket("[EPIC] b"), "nodispatch_marker")
    park(add_ticket("[FRANK GATE] c"), "frank_gated")
    assert dd.dispatch_gate_state() == {"enabled": True, "parked": 3,
                                        "by_reason": {"frank_gated": 2, "nodispatch_marker": 1}}

    write_config(denv, local_backends={
        "hathor-llama8b": {"health_url": "http://hathor.test/api/ps", "slots": 2},
        "nephthys-qwen14b": {"health_url": "http://neph.test/api/ps", "slots": 2}})
    monkeypatch.setattr(local_capacity, "http_probe", lambda url, timeout=2.0: "hathor" in url)
    monkeypatch.setattr(dd.concurrency, "is_local_unit", lambda unit: True)
    monkeypatch.setattr(dd.concurrency, "unit_model", lambda unit: "hathor-llama8b")
    slots = dd.local_backend_slots({"workers": [{"unit": "fworker-wq9", "model": "hathor-llama8b"}]})
    assert slots["hathor-llama8b"] == {"healthy": True, "slots": 2, "running": 1, "free": 1}
    assert slots["nephthys-qwen14b"]["healthy"] is False and slots["nephthys-qwen14b"]["free"] == 0

    window = {"failures": 0, "total": 0, "rate": 0.0}
    snap = {"generated_at": "t", "running": {"workers": []}, "queue_depth": {},
            "failure_rate": {"overall": {"24h": window, "7d": window}},
            "circuit_breaker": {"armed": True, "failures": 0, "total": 0,
                                "window_minutes": 90, "rate": 0.0},
            "stuck_tickets": {}, "budget": {}, "dispatch_gate": dd.dispatch_gate_state(),
            "local_backends": slots}
    html = dashboard.render_html(snap)
    assert "parked by the dispatcher (3)" in html and "<td>frank_gated</td><td>2</td>" in html
    assert "<td>hathor-llama8b</td><td>up</td><td>1/2</td><td>1</td>" in html
    assert "<td>nephthys-qwen14b</td><td>DOWN</td><td>0/2</td><td>0</td>" in html


def test_dashboard_data_reads_the_store_read_only(denv):
    import dashboard_data as dd
    assert dd.dispatch_gate_state()["by_reason"] == {}     # no table yet: reads as empty ...
    con = sqlite3.connect(denv["wq_db"])
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
    con.close()
    assert "dispatch_gate" not in names                     # ... and the read did not create it


# ---- wq#2212: never park a verdict drawn from a transient wq failure --------------------

def _failing_wq_show(monkeypatch, tid):
    real = router.wq_show

    def show(i):
        if int(i) == tid:
            raise SystemExit(f"router: no such ticket {i}")
        return real(i)
    monkeypatch.setattr(router, "wq_show", show)
    return real


def test_transient_dep_read_failure_is_not_parked(denv, monkeypatch):
    dep = add_ticket("the dep, already done")
    db_exec(denv, "UPDATE tasks SET status='done' WHERE id=?", (dep,))
    child = add_ticket("needs the dep", deps=str(dep))
    real = _failing_wq_show(monkeypatch, dep)
    assert fd.run_pass() == 0
    assert gate_store.parked_rows() == {}
    skips = [e for e in log_events(denv) if e["event"] == "skip" and e["ticket"] == child]
    assert [(e["reason"], e.get("parked")) for e in skips] == [("deps_unmet", None)]

    monkeypatch.setattr(router, "wq_show", real)   # the read recovers: dispatched, not sat out
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv) if e["event"] == "spawn"] == [child]


def test_real_unmet_dep_still_parks(denv):
    dep = add_ticket("[FRANK GATE] the dep stays open")
    add_ticket("needs the dep", deps=str(dep))
    assert fd.run_pass() == 0
    assert gate_store.by_reason() == {"frank_gated": 1, "deps_unmet": 1}


def test_transient_route_failure_is_not_parked(denv, monkeypatch):
    tid = add_ticket("unrouted", model=None)

    def boom(*a, **kw):
        raise SystemExit("router: no such ticket")
    monkeypatch.setattr(router, "assign", boom)
    assert fd.run_pass() == 0
    assert gate_store.parked_rows() == {}
    skips = [e for e in log_events(denv) if e["event"] == "skip" and e["ticket"] == tid]
    assert [(e["reason"], e.get("parked")) for e in skips] == [("route_unreadable", None)]


def test_park_fingerprints_the_snapshot_the_gate_judged(denv):
    import types
    import gates
    tid = add_ticket("[FRANK GATE] as judged")
    snap = router.wq_show(tid)
    wq("edit", str(tid), "--title", "[FRANK GATE] edited after the gate read")
    log = types.SimpleNamespace(emit=lambda *a, **k: None)
    g = types.SimpleNamespace(tid=tid, lane="testlane", t=snap, log=log,
                              ctx=types.SimpleNamespace(dry_run=False, gate_salt=""), fd=fd)
    res = gates.Fail("frank_gate", "frank_gated", fingerprint_inputs=("title",), parkable=True)
    assert gates._park(g, res)
    row = gate_store.parked_rows()[tid]
    assert row["fingerprint"] == gate_store.fingerprint(tid, ("title",), row=snap)
    assert row["fingerprint"] != gate_store.fingerprint(tid, ("title",))   # not the NEW title
    assert gate_store.is_parked(tid, gate_store.fingerprint(tid, ("title",))) is False


def test_lever_off_empties_the_janitors_inputs(denv, monkeypatch):
    tid = add_ticket("[FRANK GATE] hold")
    assert gate_store.park(tid, "repo_mismatch", gate_store.fingerprint(tid, ("title",)))
    assert set(gate_store.parked_rows()) == {tid}
    assert list(gate_store.rows_by_reason()) == ["repo_mismatch"]
    monkeypatch.setenv("FOREMAN_GATE_STORE", "off")
    assert gate_store.parked_rows() == {} and gate_store.rows_by_reason() == {}
    assert gate_store.by_reason() == {"repo_mismatch": 1}   # the read-only status view stays
