"""wq#1261 (#13 item 1, AC1) — "synthetic ticket whose probe clears after 3 iterations;
assert auto-close within one iteration, probe output attached."

Exercises the fire_probe cmd schema end to end through run_fire_ticket()'s re-verify
loop: a probe command that fails twice then passes, with no aq_escalate/alerts.db
involved at all — this is exactly the shape a hand-filed drill ticket (no aq row) needs,
which fire_gate's old aq-row-only re-verify (wq#1234) could never cover.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fworker  # noqa: E402
import fire_gate  # noqa: E402
from classifier import SUCCESS  # noqa: E402
from test_fire_mode import _args, _wire_common  # noqa: E402


def _counting_probe_cmd(counter_file, clears_after):
    """Shell command: increments counter_file each call, exits 0 once the count has
    reached clears_after, exits 1 (still on fire) before that."""
    return (
        f"python3 -c \"import pathlib,sys; "
        f"p = pathlib.Path({str(counter_file)!r}); "
        f"n = int(p.read_text()) + 1 if p.exists() else 1; "
        f"p.write_text(str(n)); "
        f"sys.exit(0 if n >= {clears_after} else 1)\""
    )


def test_probe_clearing_after_3_iterations_auto_closes_with_evidence(tmp_path, monkeypatch):
    calls = _wire_common(tmp_path, monkeypatch)
    counter_file = tmp_path / "probe_calls.txt"
    probe_cmd = _counting_probe_cmd(counter_file, clears_after=3)
    detail = "fire_probe: " + json.dumps({"cmd": probe_cmd, "clear_when_exit": 0})
    monkeypatch.setattr(fworker, "wq_show", lambda tid: {"title": "synthetic drill", "detail": detail})

    # A plan already exists with no approval on disk -> next_action() is "hold" on every
    # iteration this loop takes before the probe clears, so the loop actually iterates
    # (diagnose would bail the ticket out on a missing plan doc instead).
    fire_gate.write_plan(717, "# plan\ncurrent state: drill\n")

    spawned = []
    monkeypatch.setattr(fworker, "run_fire_child", lambda *a, **k: spawned.append(a) or SUCCESS)

    rc = fworker.run_fire_ticket(_args())

    assert rc == fworker.EXIT_OK
    assert spawned == []  # never needed to diagnose or apply anything
    assert int(counter_file.read_text()) == 3  # cleared exactly on the 3rd check
    assert len(calls["done"]) == 1
    assert calls["done"][0][0] == 717
    assert "cleared" in calls["done"][0][1]
    assert calls["blocked"] == [] and calls["released"] == []

    evidence_path = os.path.join(fworker.run_dir(717), "fire_probe_evidence.json")
    assert os.path.exists(evidence_path)
    with open(evidence_path) as f:
        evidence = json.load(f)
    assert evidence["cleared"] is True
    assert evidence["exit_code"] == 0
    assert evidence["clear_when_exit"] == 0
    assert evidence["cmd"] == probe_cmd

    # attempt-N.json recorded the outcome too (fworker.fire_fenced_attempt_count reads
    # this directory) — an auto-close must NEVER count against the attempt fence.
    assert fworker.fire_fenced_attempt_count(717) == 0


def test_probe_still_active_holds_and_does_not_close(tmp_path, monkeypatch):
    calls = _wire_common(tmp_path, monkeypatch)
    counter_file = tmp_path / "probe_calls.txt"
    probe_cmd = _counting_probe_cmd(counter_file, clears_after=999)  # never clears here
    detail = "fire_probe: " + json.dumps({"cmd": probe_cmd, "clear_when_exit": 0})
    monkeypatch.setattr(fworker, "wq_show", lambda tid: {"title": "synthetic drill", "detail": detail})
    fire_gate.write_plan(717, "# plan\ncurrent state: drill\n")

    # Stop the hold loop after a few iterations instead of running out the 24h timeout:
    # patch time.sleep to raise once the probe has been checked a few times.
    state = {"sleeps": 0}

    def fake_sleep(_s):
        state["sleeps"] += 1
        if state["sleeps"] >= 3:
            raise SystemExit("test stop")

    monkeypatch.setattr(fworker.time, "sleep", fake_sleep)

    try:
        fworker.run_fire_ticket(_args())
    except SystemExit:
        pass

    assert calls["done"] == []
    assert int(counter_file.read_text()) >= 3  # re-verified at least once per iteration
