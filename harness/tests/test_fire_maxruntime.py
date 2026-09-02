"""wq#1261 (#13 item 2/3, AC2) — "probe never clears; assert TERM at cap, row expired,
alert emitted, no respawn past attempt 2."

Three layers, each covered here:
  1. fworker's in-process watchdog (fire_gate.runtime_exceeded) blocks the ticket with
     an EXPIRED reason once max_runtime_s elapses, without ever clearing.
  2. router.dispatch_cmd wraps the fire-response session in an external `timeout
     --signal=TERM ...` at the SAME cap, for when fworker's own loop can't be trusted
     to reach the in-process check at all (a wedged fworker) — see test_router.py's
     wq#1261 case for that half.
  3. Three "expired"/rework attempts in a row trip the attempt fence: the 3rd run
     never spawns fire-mode again, it escalates straight to FRANK.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fworker  # noqa: E402
import fire_gate  # noqa: E402
from test_fire_mode import _args, _wire_common, PROBE_DETAIL  # noqa: E402


def test_max_runtime_exceeded_blocks_as_expired_with_alert(tmp_path, monkeypatch):
    calls = _wire_common(tmp_path, monkeypatch)
    # A probe that always reports "still active" — cmd schema, no aq db involved.
    detail = "fire_probe: " + json.dumps({"cmd": "false", "clear_when_exit": 0})
    monkeypatch.setattr(fworker, "wq_show", lambda tid: {"title": "never clears", "detail": detail})
    fire_gate.write_plan(717, "# plan\ncurrent state: still on fire\n")

    # Force the very first runtime_exceeded() check to fire, by starting "now" already
    # past the cap: monkeypatch time.time to jump forward once past the initial start_ts
    # capture inside run_fire_ticket().
    t = {"now": 1_000_000.0}

    def fake_time():
        return t["now"]
    monkeypatch.setattr(fworker.time, "time", fake_time)

    real_sleep_target = [None]

    def advancing_sleep(_s):
        t["now"] += 10_000  # jump well past any max_runtime_s on the next check
    monkeypatch.setattr(fworker.time, "sleep", advancing_sleep)

    rc = fworker.run_fire_ticket(_args())

    assert rc == fworker.EXIT_FIRE_MAX_RUNTIME
    assert len(calls["blocked"]) == 1
    tid, reason = calls["blocked"][0]
    assert tid == 717
    assert reason.startswith("EXPIRED:")
    assert calls["done"] == [] and calls["released"] == []
    assert any("max runtime" in title.lower() for title, _ in calls["ntfy"])

    # attempt-N.json recorded fire_outcome=expired -> counts against the attempt fence
    assert fworker.fire_fenced_attempt_count(717) == 1


def test_attempt_fence_escalates_to_frank_on_third_expired_attempt(tmp_path, monkeypatch):
    calls = _wire_common(tmp_path, monkeypatch)
    monkeypatch.setattr(fworker, "wq_show", lambda tid: {"title": "t", "detail": PROBE_DETAIL})

    # Two PRIOR attempts already recorded as expired (as test above would produce) —
    # simulate them directly via write_attempt_state, matching run_fire_ticket()'s own
    # record shape, so this test isolates the fence check from the runtime-cap mechanics.
    for n in (1, 2):
        fworker.write_attempt_state(717, n, {
            "ticket": 717, "attempt": n, "archetype": fworker.FIRE_ARCHETYPE,
            "fire_outcome": "expired",
        })
    assert fworker.fire_fenced_attempt_count(717) == 2

    spawned = []
    monkeypatch.setattr(fworker, "run_fire_child", lambda *a, **k: spawned.append(a))
    monkeypatch.setattr(fworker, "make_worktree",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError(
                             "3rd attempt must escalate before ever making a worktree")))

    rc = fworker.run_fire_ticket(_args())

    assert rc == fworker.EXIT_FIRE_ATTEMPT_FENCE
    assert spawned == []
    assert len(calls["blocked"]) == 1
    tid, reason = calls["blocked"][0]
    assert tid == 717 and "FRANK" in reason and "fence" in reason.lower()
    assert any("escalated" in title.lower() for title, _ in calls["ntfy"])
    assert calls["done"] == [] and calls["released"] == []
    # a 3rd escalation is itself terminal, not counted as a 4th fire attempt
    assert fworker.fire_fenced_attempt_count(717) == 2


def test_below_fence_still_runs_fire_mode(tmp_path, monkeypatch):
    calls = _wire_common(tmp_path, monkeypatch)
    monkeypatch.setattr(fworker, "wq_show", lambda tid: {"title": "t", "detail": PROBE_DETAIL})
    fworker.write_attempt_state(717, 1, {
        "ticket": 717, "attempt": 1, "archetype": fworker.FIRE_ARCHETYPE,
        "fire_outcome": "expired",
    })
    assert fworker.fire_fenced_attempt_count(717) == 1  # one prior failure, fence is 2

    from classifier import SUCCESS
    monkeypatch.setattr(fworker, "run_fire_child",
                         lambda phase, *a, **k: fire_gate.write_plan(717, "# plan\n") or SUCCESS)

    db_path = str(tmp_path / "no-alerts.db")
    monkeypatch.setattr(fire_gate, "AQ_DB", db_path)

    # let it run one hold iteration then stop via the timeout path being exceeded
    monkeypatch.setattr(fworker.time, "sleep", lambda s: None)
    fire_gate.write_plan(717, "# plan\ncurrent state: x\n")
    monkeypatch.setattr(fire_gate, "runtime_exceeded", lambda *a, **k: True)

    rc = fworker.run_fire_ticket(_args())

    assert rc == fworker.EXIT_FIRE_MAX_RUNTIME  # ran fire-mode at all, unlike the fenced case
    assert fworker.fire_fenced_attempt_count(717) == 2
