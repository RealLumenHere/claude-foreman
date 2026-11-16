"""wq#5007 end to end: the dual-condition exit gate on the real fworker dispatch path
(scratch repo + scratch wq DB + tests/fake_claude.py), zero Anthropic quota."""
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_fworker_integration import (  # noqa: E402
    make_scratch_repo, make_scratch_env, wq_add, wq_status, run_fworker)


def run_scenario(tmp_path, scenario, extra=None):
    repo = make_scratch_repo(tmp_path)
    spawn_log = str(tmp_path / "spawns.log")
    env, _db, _ev, runs_root, worktrees_root, _st = make_scratch_env(
        tmp_path, 0, scenario, extra=dict({"FAKE_CLAUDE_SPAWN_LOG": spawn_log}, **(extra or {})))
    ticket_id = wq_add(env, f"exit gate {scenario}")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)
    result = run_fworker(env, ticket_id, repo, scenario)
    spawns = open(spawn_log).read().split() if os.path.exists(spawn_log) else []
    records = [json.load(open(p)) for p in
               sorted(glob.glob(os.path.join(runs_root, f"wq{ticket_id}", "attempt-*.json")))]
    return result, env, ticket_id, spawns, records, runs_root, worktrees_root


def test_both_conditions_met_exits_in_one_loop(tmp_path):
    result, env, tid, spawns, records, runs_root, _ = run_scenario(tmp_path, "exit_signal_ok")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wq_status(env, tid) == "done" and spawns == ["fresh"]
    gate = records[-1]["exit_gate"]
    assert gate["enforced"] is True and gate["exit"] is True and gate["signal"] is True
    assert len(gate["indicators"]) >= 2 and gate["backup_ref"]
    cb = json.load(open(os.path.join(runs_root, f"wq{tid}", "circuit-breaker.json")))
    assert cb["state"] == "CLOSED"


def test_missing_signal_costs_one_nudge_not_an_attempt(tmp_path):
    result, env, tid, spawns, records, _, _ = run_scenario(tmp_path, "exit_signal_missing_then_ok")
    assert result.returncode == 0, result.stdout + result.stderr
    assert spawns == ["fresh", "resume"]
    assert "exit NOT accepted (signal_missing" in result.stderr + result.stdout
    assert wq_status(env, tid) == "done"
    assert [r["attempt"] for r in records] == [1]            # same attempt number
    assert records[-1]["exit_gate"]["exit"] is True
    assert records[-1]["classification"] == "SUCCESS"


def test_signal_never_given_is_never_plain_success_and_verified_work_is_not_lost(tmp_path):
    """Exactly one nudge. Still no EXIT_SIGNAL, but the ticket is done and verified: the
    safety valve ends the loop as DEGRADED_SUCCESS (never SUCCESS on one condition, never
    a discarded landing over a formatting omission)."""
    result, env, tid, spawns, records, _, _ = run_scenario(tmp_path, "exit_signal_never")
    assert spawns == ["fresh", "resume"]
    assert records[-1]["classification"] == "DEGRADED_SUCCESS"
    gate = records[-1]["exit_gate"]
    assert gate["exit"] is False and gate["reason"] == "signal_missing"
    assert gate["safety_valve"] is True
    assert result.returncode == 0 and wq_status(env, tid) == "done"


def test_explicit_false_is_never_accepted_even_on_a_done_ticket(tmp_path):
    """The valve is for a MISSING signal only. A worker that says EXIT_SIGNAL: false is
    never scored a success, nudge or no nudge."""
    result, env, tid, spawns, records, _, _ = run_scenario(tmp_path, "exit_signal_false_done")
    assert spawns == ["fresh", "resume"]
    assert records[-1]["classification"] == "FALSE_COMPLETE"
    assert records[-1]["exit_gate"]["reason"] == "signal_false"
    assert "safety_valve" not in records[-1]["exit_gate"]


def test_a_pre_contract_worker_costs_one_nudge(tmp_path):
    """The stock `success` scenario with the contract switched off = a worker that never
    learned the protocol: one extra loop, then the valve; the ticket still closes."""
    result, env, tid, spawns, records, _, _ = run_scenario(
        tmp_path, "success", extra={"FAKE_CLAUDE_NO_EXIT_SIGNAL": "1"})
    assert result.returncode == 0 and wq_status(env, tid) == "done"
    assert records[-1]["classification"] == "DEGRADED_SUCCESS"
    assert records[-1]["exit_gate"]["safety_valve"] is True


def test_explicit_false_blocks_a_close_on_a_commit_that_only_names_the_ticket(monkeypatch):
    import types
    import fworker
    calls = []
    monkeypatch.setattr(fworker, "check_artifacts_done", lambda t, w: (True, "abc123def"))
    monkeypatch.setattr(fworker, "wq_done", lambda *a, **k: calls.append("done"))
    monkeypatch.setattr(fworker, "file_verify_pass", lambda *a, **k: calls.append("verify"))
    monkeypatch.setattr(fworker, "write_prior_attempt_notes", lambda *a, **k: None)
    monkeypatch.setattr(fworker, "charged_attempt_count", lambda *a, **k: 99)
    monkeypatch.setattr(fworker, "handle_failure", lambda *a, **k: "FAILED")
    monkeypatch.setattr(fworker, "log", lambda m: None)
    args = types.SimpleNamespace(ticket=1, model="m")
    state = types.SimpleNamespace(session_id="s", exit_gate_explicit_false=True)
    assert fworker.handle_resumable_failure(
        args, 1, 1, fworker.FALSE_COMPLETE, state, [], "/wt") == "FAILED"
    assert calls == []
    state.exit_gate_explicit_false = False
    assert fworker.handle_resumable_failure(
        args, 1, 1, fworker.FALSE_COMPLETE, state, [], "/wt") == fworker.EXIT_OK
    assert calls == ["done", "verify"]


def test_shadow_mode_records_without_gating(tmp_path):
    result, env, tid, spawns, records, _, _ = run_scenario(
        tmp_path, "exit_signal_never", extra={"FOREMAN_EXIT_GATE": "shadow"})
    assert result.returncode == 0 and spawns == ["fresh"]
    assert records[-1]["classification"] == "SUCCESS"
    assert records[-1]["exit_gate"]["exit"] is False and records[-1]["exit_gate"]["enforced"] is False


def test_off_mode_is_the_pre_wq5007_harness(tmp_path):
    result, env, tid, spawns, records, runs_root, _ = run_scenario(
        tmp_path, "exit_signal_never", extra={"FOREMAN_EXIT_GATE": "off"})
    assert result.returncode == 0 and spawns == ["fresh"]
    assert records[-1]["classification"] == "SUCCESS"
    assert not os.path.exists(os.path.join(runs_root, f"wq{tid}", "circuit-breaker.json"))


def test_open_circuit_refuses_before_the_claim(tmp_path):
    repo = make_scratch_repo(tmp_path)
    spawn_log = str(tmp_path / "spawns.log")
    env, _db, _ev, runs_root, _wt, _st = make_scratch_env(
        tmp_path, 0, "exit_signal_ok", extra={"FAKE_CLAUDE_SPAWN_LOG": spawn_log})
    tid = wq_add(env, "open circuit")
    env["FAKE_CLAUDE_TICKET"] = str(tid)
    rd = os.path.join(runs_root, f"wq{tid}")
    os.makedirs(rd)
    import time
    json.dump({"state": "OPEN", "opened_at": time.time(), "reason": "no progress 3 loops"},
              open(os.path.join(rd, "circuit-breaker.json"), "w"))
    result = run_fworker(env, tid, repo, "exit_signal_ok")
    assert "DEFERRED" in result.stdout and "circuit OPEN" in result.stderr + result.stdout
    assert not os.path.exists(spawn_log)
    assert wq_status(env, tid) == "open"
    # cooldown elapsed -> half-open probe runs, exits cleanly, circuit closes
    json.dump({"state": "OPEN", "opened_at": time.time() - 31 * 60, "reason": "x"},
              open(os.path.join(rd, "circuit-breaker.json"), "w"))
    result = run_fworker(env, tid, repo, "exit_signal_ok")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.load(open(os.path.join(rd, "circuit-breaker.json")))["state"] == "CLOSED"


def test_hourly_ticket_limit_defers_and_releases(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, _db, _ev, runs_root, _wt, _st = make_scratch_env(
        tmp_path, 0, "exit_signal_ok", extra={"FOREMAN_MAX_TICKET_CALLS_PER_HOUR": "1"})
    tid = wq_add(env, "rate limited")
    env["FAKE_CLAUDE_TICKET"] = str(tid)
    os.makedirs(runs_root, exist_ok=True)
    import time
    json.dump({"hour": time.strftime("%Y%m%d%H", time.gmtime()), "global": 1,
               "tickets": {f"wq{tid}": 1}},
              open(os.path.join(runs_root, ".exit-gate-calls.json"), "w"))
    result = run_fworker(env, tid, repo, "exit_signal_ok")
    assert "DEFERRED" in result.stdout and "ticket call limit" in result.stderr + result.stdout
    assert wq_status(env, tid) == "open"
