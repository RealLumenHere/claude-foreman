"""wq#1261 (#13 item 4, AC4) — live drill: "one synthetic fire through the real stack,
closed within 15 minutes of the condition clearing," plus the ticket's own text: "a
drill that proves a cleared condition auto-closes and an over-cap fire blocks cleanly."

"Real stack" here means the same thing every other test in this suite means it: the
real `wq`/`slock` binaries against a scratch WQ_DB (not mocked), the real dispatcher
decision pipeline (`foreman_dispatcher.run_pass`), and the real `fire_gate`/
`fworker.run_fire_ticket()` state machine — only `lumen-run` (no real systemd unit),
`make_worktree` (no real git clone) and the `claude` child itself are faked, exactly
like test_dispatcher.py's `denv` fixture and test_fire_mode.py's `_wire_common` already
do for the rest of this suite. Nothing here spends model quota or touches the live
fleet wq database (WQ_DB is repointed at a scratch file for the whole test).
"""
import json
import os
import sys
import time
import types

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
REPO = os.path.dirname(DISPATCHER_DIR)
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, DISPATCHER_DIR)
sys.path.insert(0, os.path.join(REPO, "harness"))
import foreman_dispatcher as fd  # noqa: E402
import fworker  # noqa: E402
import fire_gate  # noqa: E402
from classifier import SUCCESS  # noqa: E402
from test_dispatcher import denv, add_ticket, log_events  # noqa: E402

DRILL_BUDGET_S = 15 * 60  # AC4's own bar: closed within 15 minutes of clearing


def _real_wq(tid, subcmd):
    """Real `wq` subprocess call against the scratch WQ_DB `denv` already pointed
    WQ_DB/PATH at — same binary fworker.py itself shells out to."""
    import subprocess
    return subprocess.run([fworker.WQ_BIN, subcmd, str(tid)], capture_output=True, text=True)


def _isolate_fire_gate_dirs(monkeypatch, tmp_path):
    # fire_gate.PLANS_DIR/APPROVALS_DIR default to /home/lumen/foreman/{plans,approvals}
    # (FOREMAN_ROOT) — a SEPARATE pair of constants from foreman_dispatcher's own
    # APPROVALS_DIR that `denv` already isolates. Must patch these too, or a drill
    # accidentally writes real plan/approval files into the live primary tree.
    monkeypatch.setattr(fire_gate, "PLANS_DIR", str(tmp_path / "fg_plans"))
    monkeypatch.setattr(fire_gate, "APPROVALS_DIR", str(tmp_path / "fg_approvals"))


def test_drill_cleared_condition_auto_closes_through_the_real_stack(tmp_path, denv, monkeypatch):
    _isolate_fire_gate_dirs(monkeypatch, tmp_path)
    marker = tmp_path / "condition-cleared"  # absent = still on fire
    detail = "fire_probe: " + json.dumps({"cmd": f"test -f {marker}", "clear_when_exit": 0})
    tid = add_ticket("[drill] wq1261 fire auto-close", lane="fleet-fires", detail=detail)
    (denv["approvals"] / f"wq{tid}.approved").write_text("")  # Frank-gate satisfied

    # Stage 1: the REAL dispatcher decision pipeline approves this ticket for dispatch —
    # proves the no-probe refusal (stage 5b) does NOT fire for a well-formed probe, and
    # every other gate (Frank, deps, budget, concurrency, preflight) clears too.
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]

    # Stage 2: the condition actually clears (mirrors sentinel's edge-clear / a real
    # fix landing) — THEN the fire-response worker (what that spawn would have started)
    # runs its re-verify loop for real, against the same scratch wq db.
    t0 = time.time()
    marker.write_text("cleared")

    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path / "_runs"))
    monkeypatch.setattr(fworker, "make_worktree",
                         lambda repo, t, a, title, arch: (str(tmp_path / "wt"), "branch"))
    monkeypatch.setattr(fworker, "run_fire_child", lambda *a, **k: SUCCESS)
    monkeypatch.setattr(fworker, "ntfy", lambda *a, **k: None)

    args = types.SimpleNamespace(
        ticket=tid, repo=str(tmp_path),
        model="claude-sonnet-5", effort="high", archetype="fire-response",
        max_turns=None, wall_clock_timeout=1, frame_gap_timeout=None,
        post_result_grace=None, worker_id="drill-worker", claude_bin="/bin/true",
    )
    rc = fworker.run_fire_ticket(args)
    elapsed = time.time() - t0

    assert rc == fworker.EXIT_OK
    assert elapsed < DRILL_BUDGET_S

    shown = _real_wq(tid, "show")
    assert shown.returncode == 0
    assert "status       : done" in shown.stdout
    assert "cleared" in shown.stdout


def test_drill_over_cap_fire_blocks_cleanly_through_the_real_stack(tmp_path, denv, monkeypatch):
    _isolate_fire_gate_dirs(monkeypatch, tmp_path)
    detail = "fire_probe: " + json.dumps({"cmd": "false", "clear_when_exit": 0})  # never clears
    tid = add_ticket("[drill] wq1261 fire max-runtime", lane="fleet-fires", detail=detail)
    (denv["approvals"] / f"wq{tid}.approved").write_text("")

    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]

    fire_gate.write_plan(tid, "# plan\ncurrent state: still on fire\n")
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path / "_runs"))
    monkeypatch.setattr(fworker, "make_worktree",
                         lambda repo, t, a, title, arch: (str(tmp_path / "wt"), "branch"))
    monkeypatch.setattr(fworker, "ntfy", lambda *a, **k: None)
    # Force the runtime cap without actually sleeping 2h of wall-clock in a test.
    monkeypatch.setattr(fire_gate, "runtime_exceeded", lambda *a, **k: True)

    args = types.SimpleNamespace(
        ticket=tid, repo=str(tmp_path),
        model="claude-sonnet-5", effort="high", archetype="fire-response",
        max_turns=None, wall_clock_timeout=1, frame_gap_timeout=None,
        post_result_grace=None, worker_id="drill-worker", claude_bin="/bin/true",
    )
    rc = fworker.run_fire_ticket(args)

    assert rc == fworker.EXIT_FIRE_MAX_RUNTIME
    shown = _real_wq(tid, "show")
    assert shown.returncode == 0
    assert "status       : blocked" in shown.stdout
    assert "EXPIRED" in shown.stdout
