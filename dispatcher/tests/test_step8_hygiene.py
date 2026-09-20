"""DISPATCH_REBUILD step 8 (wq#2132): scan, harness-defect edit witness, spawn-loop block, and lock
hygiene (lockless --dry-run, reprove outside the slock).

Reuses test_dispatcher.py's `denv` fixture and helpers (real `wq`/`slock` against a scratch DB,
the fake lumen-run), like test_local_capacity_dispatch.py.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
from test_dispatcher import (  # noqa: E402,F401
    denv, loop_guard, add_ticket, log_events, lumen_calls, ntfy_lines, write_failure_json,
    crash_pass, aq_rows, _take_lock, _lock_holder, fd, harness_preflight, runaway,
    spawn_loop_guard, self_heal, WQ_BIN, SLOCK_BIN)


def show(tid):
    out = subprocess.run([WQ_BIN, "show", str(tid)], capture_output=True, text=True).stdout
    return {k.strip(): v.strip() for k, _, v in (ln.partition(":") for ln in out.splitlines())}


def edit(tid, *args):
    p = subprocess.run([WQ_BIN, "edit", str(tid), *args], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr


def note(tid, text):
    p = subprocess.run([WQ_BIN, "note", str(tid), text], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr


def _lanes(*names):
    return {"lanes": {n: {"enabled": True, "max_per_day": 99} for n in names}}


# ---- scan: human lanes are never candidates ---------------------------------------------

def test_scan_never_emits_human_lane_tickets(denv):
    work = add_ticket("real work")
    add_ticket("Frank's chore", lane="user")
    add_ticket("solder something", lane="frank-hw")
    log = fd.DecisionLog(str(denv["tmp"] / "d.log"), "fdisp-scan")
    got = fd.scan_candidates(_lanes("testlane", "user", "frank-hw"), log)
    assert [c["id"] for c in got] == [work]


def test_legacy_lever_scans_human_lanes_again(denv, monkeypatch):
    monkeypatch.setenv("FOREMAN_DISPATCH_HYGIENE", "legacy")
    add_ticket("real work")
    add_ticket("Frank's chore", lane="user")
    log = fd.DecisionLog(str(denv["tmp"] / "d.log"), "fdisp-scan")
    assert len(fd.scan_candidates(_lanes("testlane", "user"), log)) == 2


def test_human_lane_gate_is_still_a_second_wall(denv, monkeypatch):
    """A candidate that bypasses the scan (legacy lever, a test, a future caller) still skips
    as frank_gated and never reaches a worker."""
    monkeypatch.setenv("FOREMAN_DISPATCH_HYGIENE", "legacy")
    fd.HUMAN_LANES.add("testlane")
    try:
        tid = add_ticket("a human-owned ticket")
        assert fd.run_pass() == 0
    finally:
        fd.HUMAN_LANES.discard("testlane")
    skips = log_events(denv, "skip", ticket=tid)
    assert [s["reason"] for s in skips] == ["frank_gated"] and lumen_calls(denv) == []


def test_pass_skips_human_lane_tickets_silently_but_still_runs_the_liveness_sweep(denv, monkeypatch):
    """wq#1478's auto-close hook lived in the human-lane gate; dropping the candidates must not
    drop it. The sweep hands each human-lane ticket to the hook and logs no skip line."""
    fd_cfg = json.loads(denv["cfg_path"].read_text())
    fd_cfg["lanes"]["user"] = {"enabled": True, "max_per_day": 999}
    denv["cfg_path"].write_text(json.dumps(fd_cfg))
    subprocess.run(["git", "-C", str(denv["tmp"]), "commit", "-qam", "user lane"], check=True)
    swept = []
    monkeypatch.setattr(fd, "_liveness_autoclosed", lambda tid, t, log: swept.append(tid) or False)
    work = add_ticket("real work")
    chore = add_ticket("Frank's chore", lane="user")

    assert fd.run_pass() == 0

    assert swept == [chore]
    assert log_events(denv, "skip", ticket=chore) == []
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [work]
    assert log_events(denv, "pass_end")[0]["candidates"] == 1


# ---- harness_defect_hold: a failure older than an edit no longer holds ------------------

def _touch_newer(path):
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))


def test_edit_after_the_failure_lifts_the_hold(denv):
    tid = add_ticket("defect, then retyped", model="claude-sonnet-5")
    write_failure_json(denv, tid)
    seen = {}
    assert tid in runaway.harness_defect_ticket_ids(seen)  # first observation: held, recorded
    assert seen[str(tid)]["fp"].startswith("claude-sonnet-5|")
    assert tid in runaway.harness_defect_ticket_ids(seen)  # unchanged: still held

    edit(tid, "--archetype", "probe-build")

    assert tid not in runaway.harness_defect_ticket_ids(seen)
    assert tid not in runaway.harness_defect_ticket_ids(seen)  # and it stays lifted


def test_a_newer_failure_after_the_edit_holds_again(denv):
    tid = add_ticket("retyped, still broken", model="claude-sonnet-5")
    write_failure_json(denv, tid)
    seen = {}
    runaway.harness_defect_ticket_ids(seen)
    edit(tid, "--archetype", "probe-build")
    assert tid not in runaway.harness_defect_ticket_ids(seen)

    fpath = denv["runs_root"] / f"wq{tid}" / "failure.json"
    write_failure_json(denv, tid, summary="still CITED_PATH_UNREACHABLE")
    _touch_newer(fpath)

    assert tid in runaway.harness_defect_ticket_ids(seen)
    assert seen[str(tid)]["fp"].endswith("|probe-build|")  # re-recorded under the new pin


def test_no_witness_and_legacy_mode_keep_the_old_rule(denv, monkeypatch):
    tid = add_ticket("defect", model="claude-sonnet-5")
    write_failure_json(denv, tid)
    seen = {}
    runaway.harness_defect_ticket_ids(seen)
    edit(tid, "--archetype", "probe-build")
    assert tid in runaway.harness_defect_ticket_ids()  # no witness given: held
    monkeypatch.setenv("FOREMAN_CHARGE_MODE", "legacy")
    assert tid in runaway.harness_defect_ticket_ids(seen)  # lever: held despite the edit


def test_witness_entry_is_dropped_once_the_failure_json_is_gone(denv):
    tid = add_ticket("defect, later fixed")
    write_failure_json(denv, tid)
    seen = {}
    runaway.harness_defect_ticket_ids(seen)
    assert str(tid) in seen
    write_failure_json(denv, tid, triage="GENUINE_BUG")
    assert runaway.harness_defect_ticket_ids(seen) == {} and seen == {}


def test_pass_lifts_the_hold_after_an_edit_and_persists_the_witness(denv):
    tid = add_ticket("defect, then retyped", model="claude-sonnet-5")
    write_failure_json(denv, tid)

    assert fd.run_pass() == 0
    assert [s["reason"] for s in log_events(denv, "skip", ticket=tid)] == ["harness_defect_hold"]
    assert str(tid) in json.loads((denv["tmp"] / "dstate.json").read_text())["harness_defect_seen"]

    edit(tid, "--archetype", "probe-build")
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


def test_dry_run_records_no_witness(denv):
    tid = add_ticket("defect")
    write_failure_json(denv, tid)
    assert fd.run_pass(dry_run=True) == 0
    assert not (denv["tmp"] / "dstate.json").exists()


# ---- spawn loop: the second trip inside 24 h blocks the ticket --------------------------

def _age_burst_out(denv, tid, trip_age_h=2):
    """Roll the 3 h spawn window past the first burst and back-date its recorded trip."""
    path = denv["tmp"] / "dstate.json"
    st = json.loads(path.read_text())
    then = datetime.now(timezone.utc)
    for e in st["spawn_ledger"][str(tid)]:
        e["ts"] = (then - timedelta(hours=9)).isoformat(timespec="seconds")
    st["spawn_loop_trips"][str(tid)] = [
        (then - timedelta(hours=trip_age_h)).isoformat(timespec="seconds")]
    path.write_text(json.dumps(st))


def test_one_loop_episode_is_one_trip_however_many_passes_it_lasts(denv, loop_guard):
    tid = add_ticket("loops forever")
    for _ in range(5):
        crash_pass(denv)
    st = json.loads((denv["tmp"] / "dstate.json").read_text())
    assert len(st["spawn_loop_trips"][str(tid)]) == 1
    assert show(tid)["status"] == "open"


def test_second_trip_blocks_with_the_last_release_note(denv, loop_guard):
    tid = add_ticket("crashes before it can write attempt-N.json")
    note(tid, "released: worker died before attempt-1 (SIGKILL)")
    for _ in range(3):
        crash_pass(denv)
    assert show(tid)["status"] == "open"  # trip 1: skipped, not blocked
    assert log_events(denv, "spawn_loop_blocked") == []

    _age_burst_out(denv, tid)
    for _ in range(3):
        crash_pass(denv)  # two fresh spawns, then the second trip

    row = show(tid)
    assert row["status"] == "blocked"
    assert row["block_reason"].startswith("SPAWN_LOOP: ")
    assert "released: worker died before attempt-1 (SIGKILL)" in row["block_reason"]
    ev = log_events(denv, "spawn_loop_blocked", ticket=tid)
    assert len(ev) == 1 and ev[0]["trips_24h"] == 2
    assert "Blocked it" in ntfy_lines(denv)[-1]  # a block pages even inside the 24 h ntfy dedup
    crash_pass(denv)  # blocked: no longer a candidate, no more lines
    assert len(log_events(denv, "skip", ticket=tid)) == 2  # one per trip pass, none after


def test_a_trip_older_than_24h_does_not_count(denv, loop_guard):
    tid = add_ticket("looped yesterday")
    for _ in range(3):
        crash_pass(denv)
    _age_burst_out(denv, tid, trip_age_h=30)
    for _ in range(3):
        crash_pass(denv)
    assert show(tid)["status"] == "open"


def test_charge_mode_legacy_only_skips(denv, loop_guard, monkeypatch):
    monkeypatch.setenv("FOREMAN_CHARGE_MODE", "legacy")
    tid = add_ticket("legacy")
    for _ in range(3):
        crash_pass(denv)
    st = json.loads((denv["tmp"] / "dstate.json").read_text())
    assert not st.get("spawn_loop_trips", {}).get(str(tid))
    assert show(tid)["status"] == "open"


INFO = {"spawns": 3, "since": "2026-09-20T10:00:00+00:00", "attempts": 0,
        "trips_24h": 2, "new_trip": True}


def test_dry_run_reports_the_block_without_doing_it(denv):
    tid = add_ticket("loops")
    log = fd.DecisionLog(str(denv["tmp"] / "d.log"), "fdisp-x")
    state = fd.load_state()
    fd.handle_spawn_loop(state, tid, "testlane", INFO, True, log, t={"notes": "released: boom"})
    assert [e["reason"] for e in log.events if e["event"] == "would_block"] == ["SPAWN_LOOP: released: boom"]
    assert show(tid)["status"] == "open" and not state.get("spawn_loop_trips")


def test_a_failed_block_is_logged_and_still_pages(denv, monkeypatch):
    tid = add_ticket("loops")
    monkeypatch.setattr(spawn_loop_guard, "block_ticket", lambda *a, **k: False)
    log = fd.DecisionLog(str(denv["tmp"] / "d.log"), "fdisp-x")
    fd.handle_spawn_loop(fd.load_state(), tid, "testlane", INFO, False, log, t={"notes": ""})
    assert [e["reason"] for e in log.events if e["event"] == "error"] == ["block_failed"]
    assert len(ntfy_lines(denv)) == 1


def test_release_note_picks_the_last_release_line():
    notes = "[t] claimed\n[t] released: first\n[t] progress\n[t] released: second\n[t] tail"
    assert spawn_loop_guard.release_note(notes).endswith("released: second")
    assert spawn_loop_guard.release_note("[t] only a note") == "[t] only a note"
    assert spawn_loop_guard.release_note(None) == "no release note"
    assert len(spawn_loop_guard.release_note("x" * 999)) == 300


def test_looping_counts_episodes_not_passes():
    now = datetime.now(timezone.utc)
    ts = lambda h: (now - timedelta(hours=h)).isoformat(timespec="seconds")  # noqa: E731
    ledger = {"7": [{"ts": ts(2.5), "attempts": 0}, {"ts": ts(2), "attempts": 0},
                    {"ts": ts(1), "attempts": 0}]}
    fresh = {"spawn_ledger": ledger}
    info = spawn_loop_guard.looping(fresh, 7, now=now, threshold=3)
    assert (info["trips_24h"], info["new_trip"]) == (1, True)
    recorded = {"spawn_ledger": ledger, "spawn_loop_trips": {"7": [ts(0.5)]}}
    info = spawn_loop_guard.looping(recorded, 7, now=now, threshold=3)
    assert (info["trips_24h"], info["new_trip"]) == (1, False)       # same episode
    earlier = {"spawn_ledger": ledger, "spawn_loop_trips": {"7": [ts(5)]}}
    info = spawn_loop_guard.looping(earlier, 7, now=now, threshold=3)
    assert (info["trips_24h"], info["new_trip"]) == (2, True)        # a second episode
    stale = {"spawn_ledger": ledger, "spawn_loop_trips": {"7": [ts(30)]}}
    assert spawn_loop_guard.looping(stale, 7, now=now, threshold=3)["trips_24h"] == 1


def test_trips_are_pruned_on_load(denv):
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat(timespec="seconds")
    new = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (denv["tmp"] / "dstate.json").write_text(json.dumps(
        {"day": "2000-01-01", "spawn_loop_trips": {"1": [old], "2": [old, new]},
         "harness_defect_seen": {"3": {"fp": "x", "mtime_ns": 1}}}))
    st = fd.load_state()
    assert st["spawn_loop_trips"] == {"2": [new]}
    assert st["harness_defect_seen"] == {"3": {"fp": "x", "mtime_ns": 1}}


# ---- lock hygiene: --dry-run takes no lock ----------------------------------------------

def test_dry_run_takes_no_lock(denv, monkeypatch):
    add_ticket("dry run me")
    inside = []
    real = fd._do_pass
    monkeypatch.setattr(fd, "_do_pass", lambda *a, **k: inside.append(_lock_holder()) or real(*a, **k))
    assert fd.run_pass(dry_run=True) == 0
    assert "foreman-dispatcher" not in inside[0]
    assert log_events(denv, "would_spawn") and lumen_calls(denv) == []


def test_dry_run_is_not_refused_by_a_live_pass_and_does_not_disturb_it(denv, monkeypatch):
    add_ticket("dry run beside a live pass")
    _take_lock(f"foreman-dispatcher:{os.getpid() + 1}")
    monkeypatch.setattr(fd, "_pass_pid_alive", lambda pid: True)

    assert fd.run_pass(dry_run=True) == 0

    assert [h for h in log_events(denv, "halt") if h["reason"] == "lock_busy"] == []
    assert log_events(denv, "would_spawn")
    assert f"foreman-dispatcher:{os.getpid() + 1}" in _lock_holder()  # the live holder keeps it
    assert fd.run_pass() == 0  # while a REAL pass is still refused
    assert [h["reason"] for h in log_events(denv, "halt")] == ["lock_busy"]


def test_legacy_lever_makes_dry_run_take_the_lock_again(denv, monkeypatch):
    monkeypatch.setenv("FOREMAN_DISPATCH_HYGIENE", "legacy")
    add_ticket("legacy dry run")
    _take_lock(f"foreman-dispatcher:{os.getpid() + 1}")
    monkeypatch.setattr(fd, "_pass_pid_alive", lambda pid: True)
    assert fd.run_pass(dry_run=True) == 0
    assert [h["reason"] for h in log_events(denv, "halt")] == ["lock_busy"]


# ---- lock hygiene: the stale-stamp reprove runs after slock release ----------------------

STALE = "stale preflight stamp for repo"


def _ok(repo):
    return harness_preflight.PreflightResult(repo, ok=True, reason="ok", static_failures=[], denials=[])


def _denied(repo):
    return harness_preflight.PreflightResult(
        repo, ok=False, reason="live proof found permission denials", static_failures=[],
        denials=["denied"])


def _stale(monkeypatch):
    monkeypatch.setattr(harness_preflight, "dispatch_gate_check", lambda repo: (False, STALE))


def _event_index(denv, pred):
    return next(i for i, e in enumerate(log_events(denv)) if pred(e))


def test_reprove_runs_after_the_lock_is_released_and_the_ticket_waits_a_pass(denv, monkeypatch):
    tid = add_ticket("stale stamp, clean reprove")
    _stale(monkeypatch)
    holders = []
    monkeypatch.setattr(harness_preflight, "preflight",
                        lambda repo: holders.append(_lock_holder()) or _ok(repo))

    assert fd.run_pass() == 0

    assert len(holders) == 1 and "foreman-dispatcher" not in holders[0]  # lock already free
    skips = log_events(denv, "skip", ticket=tid)
    assert [s["reason"] for s in skips] == ["no_fresh_stamp"]  # one decision line
    assert "after the pass lock is released" in skips[0]["detail"]
    assert lumen_calls(denv) == []  # this pass did not spawn it
    reproved = _event_index(denv, lambda e: e.get("reason") == "auto_reproved")
    assert reproved > _event_index(denv, lambda e: e["event"] == "pass_end")

    monkeypatch.setattr(harness_preflight, "dispatch_gate_check", lambda repo: (True, ""))
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]  # the next pass spawns it


def test_many_tickets_of_one_stale_repo_cost_one_reprove(denv, monkeypatch):
    ids = [add_ticket(f"stale {i}") for i in range(3)]
    _stale(monkeypatch)
    calls = []
    monkeypatch.setattr(harness_preflight, "preflight", lambda repo: calls.append(repo) or _ok(repo))
    assert fd.run_pass() == 0
    assert len(calls) == 1
    assert sorted(e["ticket"] for e in log_events(denv, "skip")) == sorted(ids)


def test_failed_reprove_pages_once_logs_info_and_starts_the_cooldown(denv, monkeypatch):
    tid = add_ticket("stale stamp, still denied")
    _stale(monkeypatch)
    calls = []
    monkeypatch.setattr(harness_preflight, "preflight", lambda repo: calls.append(repo) or _denied(repo))

    assert fd.run_pass() == 0

    assert [s["reason"] for s in log_events(denv, "skip", ticket=tid)] == ["no_fresh_stamp"]
    failed = [e for e in log_events(denv, "info") if e.get("reason") == "reprove_failed"]
    assert len(failed) == 1 and "denied" in failed[0]["detail"]
    lines = ntfy_lines(denv)
    assert len(lines) == 1 and "reprove failed" in lines[0]
    assert "repo" in json.loads((denv["tmp"] / "dstate.json").read_text())["preflight_ntfy"]

    assert fd.run_pass() == 0  # inside the 1 h cooldown: no second live proof, no second page
    assert len(calls) == 1 and len(ntfy_lines(denv)) == 1


def test_post_lock_commit_does_not_clobber_another_passes_state(denv, monkeypatch):
    """The reprove runs for minutes with the lock free; a pass that ran meanwhile wrote its
    lane counts to state.json, and the reprove's cooldown write must keep them."""
    add_ticket("stale stamp")
    _stale(monkeypatch)
    path = denv["tmp"] / "dstate.json"

    def other_pass_wrote_state(repo):
        st = fd.load_state()
        st["lane_counts"] = {"testlane": 7}
        path.write_text(json.dumps(st))
        return _denied(repo)
    monkeypatch.setattr(harness_preflight, "preflight", other_pass_wrote_state)

    assert fd.run_pass() == 0

    st = json.loads(path.read_text())
    assert st["lane_counts"] == {"testlane": 7} and "repo" in st["preflight_ntfy"]


def test_a_reprove_already_running_elsewhere_is_left_to_it(denv, monkeypatch):
    add_ticket("stale stamp")
    _stale(monkeypatch)
    calls = []
    monkeypatch.setattr(harness_preflight, "preflight", lambda repo: calls.append(repo) or _ok(repo))
    held = subprocess.run([SLOCK_BIN, "acquire", "foreman-reprove-repo", "--ttl", "5",
                           "--holder", "foreman-dispatcher:other"], capture_output=True, text=True)
    assert held.returncode == 0, held.stdout + held.stderr

    assert fd.run_pass() == 0

    assert calls == []
    assert [e for e in log_events(denv, "info") if e.get("reason") == "reprove_in_flight"]


def test_reprove_lock_is_released_after_the_reprove(denv, monkeypatch):
    add_ticket("stale stamp")
    _stale(monkeypatch)
    monkeypatch.setattr(harness_preflight, "preflight", _ok)
    assert fd.run_pass() == 0
    who = subprocess.run([SLOCK_BIN, "who", "foreman-reprove-repo"], capture_output=True, text=True)
    assert "foreman-dispatcher" not in who.stdout


def test_a_raising_reprove_never_loses_the_pass_or_the_langfuse_emit(denv, monkeypatch):
    add_ticket("stale stamp")
    _stale(monkeypatch)
    monkeypatch.setattr(fd, "_sleep", lambda s: None)

    def boom(repo):
        raise RuntimeError("claude vanished")
    monkeypatch.setattr(harness_preflight, "preflight", boom)
    assert fd.run_pass() == 0
    assert log_events(denv, "pass_end")


def test_dry_run_stale_stamp_reproves_nothing(denv, monkeypatch):
    tid = add_ticket("stale stamp, dry run")
    _stale(monkeypatch)
    calls = []
    monkeypatch.setattr(harness_preflight, "preflight", lambda repo: calls.append(repo) or _ok(repo))
    assert fd.run_pass(dry_run=True) == 0
    assert calls == []
    assert [s["reason"] for s in log_events(denv, "skip", ticket=tid)] == ["no_fresh_stamp"]
    assert log_events(denv, "would_ntfy", ticket=tid)[0]["reason"] == "preflight_reprove"


def test_legacy_lever_reproves_inside_the_lock_and_spawns_the_same_pass(denv, monkeypatch):
    monkeypatch.setenv("FOREMAN_DISPATCH_HYGIENE", "legacy")
    tid = add_ticket("stale stamp, legacy")
    _stale(monkeypatch)
    holders = []
    monkeypatch.setattr(harness_preflight, "preflight",
                        lambda repo: holders.append(_lock_holder()) or _ok(repo))
    assert fd.run_pass() == 0
    assert len(holders) == 1 and "foreman-dispatcher" in holders[0]  # under the pass lock
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]
