"""wq#717 (AUTO-5) fworker-level fire-mode orchestration tests — zero quota. Mocks
run_fire_child (the one function that actually spawns a `claude -p` child) so these
exercise run_fire_ticket()'s diagnose/hold/apply/timeout state-machine wiring and its
wq lifecycle calls (release/heartbeat/ntfy) without a live model or subprocess.

wq#1234 adds re-verify + max-runtime coverage: a fire whose originating probe flips to
PASS (aq row resolved) auto-closes instead of holding, and a fire that churns past the
wall-clock cap blocks cleanly with the current probe state instead of running for hours.
"""
import json
import os
import sqlite3
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fworker  # noqa: E402
import fire_gate  # noqa: E402
from classifier import SUCCESS, TURNS  # noqa: E402


def _args(ticket=717, model="claude-sonnet-5", effort="high"):
    return types.SimpleNamespace(
        ticket=ticket, repo="/tmp/unused-repo", model=model, effort=effort,
        archetype="fire-response", max_turns=None, wall_clock_timeout=1,
        frame_gap_timeout=None, post_result_grace=None, worker_id="test-fire-worker",
        claude_bin="/bin/true",
    )


def _wire_common(tmp_path, monkeypatch):
    monkeypatch.setattr(fire_gate, "PLANS_DIR", str(tmp_path / "plans"))
    monkeypatch.setattr(fire_gate, "APPROVALS_DIR", str(tmp_path / "approvals"))
    # wq#1261: run_fire_ticket() now writes attempt-N.json (fire_fenced_attempt_count)
    # like every other archetype — isolate RUNS_ROOT so one test's attempts can't leak
    # into another's fence count (every test here reuses ticket 717).
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path / "_runs"))
    token_file = tmp_path / "fire.token"
    token_file.write_text("s3cr3t")
    monkeypatch.setattr(fire_gate, "FIRE_TOKEN_FILE", str(token_file))
    monkeypatch.setattr(fworker, "make_worktree",
                         lambda repo, tid, attempt, title, arch: (str(tmp_path / "wt"), "branch"))
    monkeypatch.setattr(fworker, "wq_show", lambda tid: {"title": "test fire ticket", "detail": "x"})
    monkeypatch.setattr(fworker.time, "sleep", lambda s: None)

    # wq#1234: no aq db on disk by default -> probe_condition_cleared fails closed
    # (False) for every existing test here, same as before this ticket's change.
    monkeypatch.setattr(fire_gate, "AQ_DB", str(tmp_path / "no-such-alerts.db"))

    calls = {"released": [], "blocked": [], "heartbeats": [], "ntfy": [], "done": []}
    monkeypatch.setattr(fworker, "wq_release", lambda tid: calls["released"].append(tid))
    monkeypatch.setattr(fworker, "wq_block", lambda tid, reason: calls["blocked"].append((tid, reason)))
    monkeypatch.setattr(fworker, "wq_heartbeat", lambda tid: calls["heartbeats"].append(tid))
    monkeypatch.setattr(fworker, "wq_done", lambda tid, notes: calls["done"].append((tid, notes)))
    monkeypatch.setattr(fworker, "ntfy", lambda title, msg, priority="default": calls["ntfy"].append((title, msg)))
    return calls


def _aq_db(tmp_path, rows):
    path = str(tmp_path / "alerts.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE alerts(id INTEGER PRIMARY KEY, status TEXT)")
    conn.executemany("INSERT INTO alerts(id, status) VALUES (?, ?)", rows)
    conn.commit()
    conn.close()
    return path


PROBE_DETAIL = "Probe: probe-maat-vault\naq row: #967 (source=probe, priority=high)\n"


def test_diagnosis_with_no_plan_written_releases(tmp_path, monkeypatch):
    calls = _wire_common(tmp_path, monkeypatch)
    monkeypatch.setattr(fworker, "run_fire_child", lambda *a, **k: TURNS)  # never writes a plan

    rc = fworker.run_fire_ticket(_args())

    assert rc == fworker.EXIT_FIRE_DIAGNOSIS_FAILED
    assert calls["released"] == [717]
    assert any("diagnosis failed" in title for title, _ in calls["ntfy"])


def test_hold_notifies_once_then_times_out(tmp_path, monkeypatch):
    calls = _wire_common(tmp_path, monkeypatch)

    def fake_child(phase, args, ticket, ticket_id, worktree, worker_id, max_turns, wall_clock, plan_text=None):
        if phase == "diagnosis":
            fire_gate.write_plan(ticket_id, "# plan\ncurrent state: ok\n")
        return SUCCESS
    monkeypatch.setattr(fworker, "run_fire_child", fake_child)

    # Force the timeout branch on the very first hold check instead of waiting 24h:
    # backdate the plan's mtime past the (tiny, test-only) timeout window.
    orig_write_plan = fire_gate.write_plan
    def write_plan_then_backdate(tid, text):
        orig_write_plan(tid, text)
        old = fire_gate.plan_path(tid)
        os.utime(old, (0, 0))
    monkeypatch.setattr(fire_gate, "write_plan", write_plan_then_backdate)

    args = _args()
    monkeypatch.setenv("FOREMAN_FIRE_TIMEOUT_HOURS", "1")
    rc = fworker.run_fire_ticket(args)

    assert rc == fworker.EXIT_FIRE_TIMEOUT
    assert calls["released"] == [717]
    assert any("timed out" in title for title, _ in calls["ntfy"])


def test_valid_approval_runs_apply_and_succeeds(tmp_path, monkeypatch):
    calls = _wire_common(tmp_path, monkeypatch)
    plan = "# plan\ncurrent state: ok\n"
    fire_gate.write_plan(717, plan)
    os.makedirs(fire_gate.APPROVALS_DIR, exist_ok=True)
    with open(fire_gate.approval_path(717), "w") as f:
        json.dump({"ticket": 717, "plan_hash": fire_gate.sha256_text(plan), "token": "s3cr3t"}, f)

    seen_phases = []
    def fake_child(phase, args, ticket, ticket_id, worktree, worker_id, max_turns, wall_clock, plan_text=None):
        seen_phases.append(phase)
        return SUCCESS
    monkeypatch.setattr(fworker, "run_fire_child", fake_child)

    rc = fworker.run_fire_ticket(_args())

    assert rc == fworker.EXIT_OK
    assert seen_phases == ["apply"]  # never re-diagnosed — a valid approval was already there
    assert calls["released"] == []


def test_apply_drift_clears_state_and_rediagnoses(tmp_path, monkeypatch):
    calls = _wire_common(tmp_path, monkeypatch)
    plan = "# plan\ncurrent state: ok\n"
    fire_gate.write_plan(717, plan)
    os.makedirs(fire_gate.APPROVALS_DIR, exist_ok=True)
    with open(fire_gate.approval_path(717), "w") as f:
        json.dump({"ticket": 717, "plan_hash": fire_gate.sha256_text(plan), "token": "s3cr3t"}, f)

    call_n = {"n": 0}
    def fake_child(phase, args, ticket, ticket_id, worktree, worker_id, max_turns, wall_clock, plan_text=None):
        call_n["n"] += 1
        if phase == "apply" and call_n["n"] == 1:
            with open(fire_gate.drift_path(ticket_id), "w") as f:
                f.write("drifted\n")
            return SUCCESS
        if phase == "diagnosis":
            fire_gate.write_plan(ticket_id, "# plan\ncurrent state: still ok\n")
            return SUCCESS
        return TURNS  # second apply attempt in this test never comes; would fail loudly
    monkeypatch.setattr(fworker, "run_fire_child", fake_child)

    # After re-diagnosis with no fresh approval, the ticket should hold — assert via
    # a sleep call that stops the test loop deterministically instead of looping forever.
    hold_reached = {"hit": False}
    def stop_on_hold(s):
        hold_reached["hit"] = True
        raise SystemExit(0)
    monkeypatch.setattr(fworker.time, "sleep", stop_on_hold)

    try:
        fworker.run_fire_ticket(_args())
    except SystemExit:
        pass

    assert hold_reached["hit"]
    assert not os.path.exists(fire_gate.drift_path(717))  # cleared by clear_fire_state
    assert any("drift" in title.lower() for title, _ in calls["ntfy"])


# --- wq#1234: re-verify auto-closes a fire whose probe flips to PASS ------------------

def test_probe_recovery_auto_closes_without_apply(tmp_path, monkeypatch):
    calls = _wire_common(tmp_path, monkeypatch)
    # aq row already resolved (sentinel edge-cleared it) before the fire worker even
    # gets its first turn — mirrors wq#1154: Maat recovered while the ticket sat idle.
    db = _aq_db(tmp_path, [(967, "resolved")])
    monkeypatch.setattr(fire_gate, "AQ_DB", db)
    monkeypatch.setattr(fworker, "wq_show", lambda tid: {"title": "t", "detail": PROBE_DETAIL})

    spawned = []
    monkeypatch.setattr(fworker, "run_fire_child", lambda *a, **k: spawned.append(a) or SUCCESS)

    rc = fworker.run_fire_ticket(_args())

    assert rc == fworker.EXIT_OK
    assert spawned == []  # never spawned a diagnosis/apply child at all
    assert calls["done"] == [(717, calls["done"][0][1])]
    assert "condition cleared" in calls["done"][0][1]
    assert calls["released"] == []
    assert calls["blocked"] == []
    assert any("auto-closed" in title.lower() for title, _ in calls["ntfy"])


def test_probe_recovery_auto_closes_mid_hold(tmp_path, monkeypatch):
    calls = _wire_common(tmp_path, monkeypatch)
    monkeypatch.setattr(fworker, "wq_show", lambda tid: {"title": "t", "detail": PROBE_DETAIL})
    db = _aq_db(tmp_path, [(967, "open")])
    monkeypatch.setattr(fire_gate, "AQ_DB", db)

    def fake_child(phase, args, ticket, ticket_id, worktree, worker_id, max_turns, wall_clock, plan_text=None):
        assert phase == "diagnosis"
        fire_gate.write_plan(ticket_id, "# plan\ncurrent state: ok\n")
        return SUCCESS
    monkeypatch.setattr(fworker, "run_fire_child", fake_child)

    # The probe recovers while the ticket is holding for approval — flip the aq row to
    # resolved on the first sleep() call (i.e. after the first hold iteration).
    conn = sqlite3.connect(db)

    def sleep_then_recover(s):
        conn.execute("UPDATE alerts SET status='resolved' WHERE id=967")
        conn.commit()
    monkeypatch.setattr(fworker.time, "sleep", sleep_then_recover)

    rc = fworker.run_fire_ticket(_args())

    assert rc == fworker.EXIT_OK
    assert calls["done"] and "condition cleared" in calls["done"][0][1]
    assert calls["released"] == []
    assert calls["blocked"] == []


# --- wq#1234: max-runtime blocks cleanly instead of churning --------------------------

def test_max_runtime_exceeded_blocks_with_probe_state(tmp_path, monkeypatch):
    calls = _wire_common(tmp_path, monkeypatch)
    monkeypatch.setattr(fworker, "wq_show", lambda tid: {"title": "t", "detail": "x"})
    monkeypatch.setenv("FOREMAN_FIRE_MAX_RUNTIME_S", "1")

    # First call establishes start_ts via time.time(); simulate wall-clock already past
    # the 1s cap by the time the loop re-checks, without a real sleep.
    real_time = fworker.time.time
    times = [real_time(), real_time() + 5]

    def fake_time():
        return times.pop(0) if times else real_time() + 5
    monkeypatch.setattr(fworker.time, "time", fake_time)

    spawned = []
    monkeypatch.setattr(fworker, "run_fire_child", lambda *a, **k: spawned.append(a) or SUCCESS)

    rc = fworker.run_fire_ticket(_args())

    assert rc == fworker.EXIT_FIRE_MAX_RUNTIME
    assert spawned == []  # never got to spawn diagnosis — capped before the first child
    assert calls["done"] == []
    assert calls["released"] == []
    assert len(calls["blocked"]) == 1
    tid, reason = calls["blocked"][0]
    assert tid == 717
    assert "max runtime" in reason.lower()
    assert any("max runtime" in title.lower() for title, _ in calls["ntfy"])
