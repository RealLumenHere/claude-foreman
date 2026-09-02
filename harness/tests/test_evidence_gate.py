"""Evidence-gated done — ISSUE-SOLUTIONS-fable.md §1 (#17 FALSE_COMPLETE). Zero quota.

The spec names one test: "fixture ticket with one deliberately failing test criterion.
(a) Canned worker emits no evidence and tries to stop -> Stop hook blocks; eventual
verify_done routes to rework; assert done was never reached and rework.json exists.
(b) Worker emits evidence claiming exit 0 for the failing test -> harness replay observes
exit 1 -> rejected. (c) Fix the test, rework -> done accepted, fence honored."

Those three are the end-to-end tests at the bottom, driven through the real fworker with
the fake_claude shim. Above them are the unit checks for each mechanism the spec's three
cases rest on: derivation (§1a), replay (§1b), the Stop hook (§1c), rework (§1d).
"""
import json
import os
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HARNESS_DIR)
sys.path.insert(0, TESTS_DIR)
import acceptance  # noqa: E402
from test_fworker_integration import (  # noqa: E402
    make_scratch_env, make_scratch_repo, run_fworker, wq_add, wq_status,
)

STOP_GATE = os.path.join(HARNESS_DIR, "hooks", "stop_gate.py")
SETTINGS = os.path.join(HARNESS_DIR, "worker-settings.json")
PROBE_CMD = "bash probe_test.sh"
FIXTURE_DETAIL = f"Make the probe pass: `{PROBE_CMD}` must exit 0."


def fixture_ticket(ticket_id=1234):
    return {"id": ticket_id, "title": "fixture ticket", "detail": FIXTURE_DETAIL}


def make_worktree(tmp_path, probe_exit=1):
    """A git worktree carrying the deliberately failing probe the criteria point at."""
    wt = tmp_path / "wt"
    wt.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=wt, check=True)
    subprocess.run(["git", "config", "user.email", "t@lumen"], cwd=wt, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=wt, check=True)
    (wt / "probe_test.sh").write_text(f"#!/bin/sh\nexit {probe_exit}\n")
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "probe"], cwd=wt, check=True)
    return wt


def pin(tmp_path, worktree, ticket=None):
    rd = tmp_path / "run"
    rd.mkdir(exist_ok=True)
    doc = acceptance.pin_acceptance(ticket or fixture_ticket(), str(rd), str(worktree),
                                    model_call=lambda t: None)
    return str(rd), doc


# ---------------------------------------------------------------------------
# §1a derivation + pinning
# ---------------------------------------------------------------------------

def test_heuristic_derivation_turns_a_named_command_into_a_test_criterion():
    criteria = acceptance.derive_criteria(fixture_ticket(), model_call=lambda t: None)
    tests = [c for c in criteria if c["proof_type"] == "test"]
    assert [c["cmd"] for c in tests] == [PROBE_CMD]
    assert tests[0]["expect_exit"] == 0
    # every checklist carries at least one citation criterion, so a ticket whose body
    # names no command still has something the harness can check against the diff.
    assert any(c["proof_type"] == "citation" for c in criteria)


def test_model_reply_is_parsed_even_when_fenced_and_chatty():
    reply = ('Sure!\n```json\n{"criteria": [{"id": "AC1", "text": "suite passes", '
             '"proof_type": "test", "cmd": "pytest -x", "expect_exit": 0}]}\n```')
    criteria = acceptance.derive_criteria(fixture_ticket(), model_call=lambda t: reply)
    assert criteria == [{"id": "AC1", "text": "suite passes", "proof_type": "test",
                         "cmd": "pytest -x", "expect_exit": 0}]


def test_unsafe_derived_command_is_demoted_never_replayed():
    reply = json.dumps({"criteria": [
        {"id": "AC1", "text": "clean up", "proof_type": "test",
         "cmd": "sudo rm -rf /var/lib/foo", "expect_exit": 0}]})
    criteria = acceptance.derive_criteria(fixture_ticket(), model_call=lambda t: reply)
    assert criteria[0]["proof_type"] == "citation"
    assert "cmd" not in criteria[0]


def test_pin_writes_both_copies_with_matching_hash(tmp_path):
    wt = make_worktree(tmp_path)
    rd, doc = pin(tmp_path, wt)
    assert doc["sha256"] == acceptance.load_pinned(rd)["sha256"]
    assert acceptance.worktree_copy_matches(rd, str(wt))


def test_edited_worktree_copy_is_detected(tmp_path):
    wt = make_worktree(tmp_path)
    rd, _ = pin(tmp_path, wt)
    (wt / acceptance.ACCEPTANCE_FILE).write_text('{"criteria": []}')
    assert not acceptance.worktree_copy_matches(rd, str(wt))


def test_dispatch_prompt_carries_the_done_contract_and_the_rework_block(tmp_path,
                                                                        monkeypatch):
    import fworker
    # RUNS_ROOT is read at import time — patch the attribute, not the env, or this test
    # writes its fixtures into the live /home/lumen/foreman/_runs.
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path / "_runs"))
    wt = make_worktree(tmp_path)
    doc = acceptance.pin_acceptance(fixture_ticket(), fworker.run_dir(1234), str(wt),
                                    model_call=lambda t: None)

    prompt = fworker.load_worker_prompt(1234, fixture_ticket(), str(wt),
                                        acceptance_doc=doc)
    assert PROBE_CMD in prompt and "evidence.json" in prompt
    assert "{{ACCEPTANCE_CONTRACT}}" not in prompt and "{{REWORK}}" not in prompt

    acceptance.write_rework(fworker.run_dir(1234), 1,
                            [{"id": "AC1", "expected": "exit 0", "observed": "exit 1",
                              "repro": PROBE_CMD, "output_tail": ""}])
    reworked = fworker.load_worker_prompt(1234, fixture_ticket(), str(wt),
                                           acceptance_doc=doc)
    assert reworked.startswith("!! PRIOR ATTEMPT REJECTED BY MACHINE REPLAY")

    # ungated dispatch: no criteria, no contract, no leftover placeholder
    bare = fworker.load_worker_prompt(1234, fixture_ticket(), str(wt), acceptance_doc=None)
    assert "EVIDENCE-GATED DONE" not in bare and "{{ACCEPTANCE_CONTRACT}}" not in bare


# ---------------------------------------------------------------------------
# §1b replay
# ---------------------------------------------------------------------------

def test_replay_rejects_evidence_claiming_exit_zero_on_a_failing_command(tmp_path):
    wt = make_worktree(tmp_path, probe_exit=1)
    rd, doc = pin(tmp_path, wt)
    evidence = {"criteria": [{"id": "AC1", "cmd_run": PROBE_CMD, "exit_code": 0},
                             {"id": "AC2", "citation": "probe_test.sh:1"}]}
    result = acceptance.verify_done(doc, evidence, str(wt), rd)
    assert not result["ok"]
    failed = {f["id"]: f for f in result["failed"]}
    assert failed["AC1"]["observed"] == "exit 1"
    assert failed["AC1"]["claimed_exit"] == 0
    # the harness's own merged-stream capture, not the model's word for it
    assert os.path.exists(os.path.join(rd, acceptance.EVIDENCE_DIR, "AC1.replay.log"))


def test_replay_accepts_true_evidence(tmp_path):
    wt = make_worktree(tmp_path, probe_exit=0)
    rd, doc = pin(tmp_path, wt)
    (wt / "probe_test.sh").write_text("#!/bin/sh\n# touched\nexit 0\n")
    evidence = {"criteria": [{"id": "AC1", "cmd_run": PROBE_CMD, "exit_code": 0},
                             {"id": "AC2", "citation": "probe_test.sh:2"}]}
    result = acceptance.verify_done(doc, evidence, str(wt), rd)
    assert result["ok"], result["failed"]
    assert result["checked"] == 2


def test_missing_criterion_is_a_failure(tmp_path):
    wt = make_worktree(tmp_path, probe_exit=0)
    rd, doc = pin(tmp_path, wt)
    result = acceptance.verify_done(doc, {"criteria": []}, str(wt), rd)
    assert {f["id"] for f in result["failed"]} == {"AC1", "AC2"}


def test_citation_must_exist_be_in_range_and_be_touched_by_the_diff(tmp_path):
    wt = make_worktree(tmp_path, probe_exit=0)
    rd, doc = pin(tmp_path, wt)
    (wt / "untouched.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add"], cwd=wt, check=True)

    def cite(value):
        ev = {"criteria": [{"id": "AC1", "cmd_run": PROBE_CMD, "exit_code": 0},
                           {"id": "AC2", "citation": value}]}
        r = acceptance.verify_done(doc, ev, str(wt), rd)
        return next((f for f in r["failed"] if f["id"] == "AC2"), None)

    assert cite("nope.py:1")["observed"] == "no such file"
    assert "outside the file" in cite("probe_test.sh:99")["observed"]
    assert "not touched" in cite("untouched.py:1")["observed"]


# ---------------------------------------------------------------------------
# §1c Stop hook
# ---------------------------------------------------------------------------

def fake_wq(tmp_path, status):
    path = tmp_path / f"fake_wq_{status}"
    path.write_text(f"#!/bin/sh\necho 'status       : {status}'\n")
    path.chmod(0o755)
    return str(path)


def run_stop_gate(tmp_path, run_dir, worktree, status="claimed", ticket="1234", env=None):
    e = dict(os.environ)
    e.update({acceptance.TICKET_ENV: ticket, acceptance.RUN_DIR_ENV: run_dir,
              "FOREMAN_WORKTREE_ROOT": str(worktree),
              "FOREMAN_WQ_BIN": fake_wq(tmp_path, status)})
    e.update(env or {})
    p = subprocess.run([sys.executable, STOP_GATE], input="{}", capture_output=True,
                       text=True, env=e, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    try:
        return json.loads(p.stdout or "{}")
    except ValueError:
        return {}


def test_stop_gate_blocks_a_stop_with_no_evidence(tmp_path):
    wt = make_worktree(tmp_path)
    rd, _ = pin(tmp_path, wt)
    out = run_stop_gate(tmp_path, rd, wt)
    assert out.get("decision") == "block"
    assert "AC1" in out["reason"] and "evidence.json" in out["reason"]


def test_stop_gate_allows_once_evidence_is_structurally_complete(tmp_path):
    wt = make_worktree(tmp_path)
    rd, _ = pin(tmp_path, wt)
    (wt / acceptance.EVIDENCE_FILE).write_text(json.dumps({"criteria": [
        {"id": "AC1", "cmd_run": PROBE_CMD, "exit_code": 0},
        {"id": "AC2", "citation": "probe_test.sh:1"}]}))
    assert run_stop_gate(tmp_path, rd, wt) == {}


def test_stop_gate_allows_when_the_ticket_is_already_done(tmp_path):
    wt = make_worktree(tmp_path)
    rd, _ = pin(tmp_path, wt)
    assert run_stop_gate(tmp_path, rd, wt, status="done") == {}


def test_stop_gate_gives_up_after_three_blocks(tmp_path):
    wt = make_worktree(tmp_path)
    rd, _ = pin(tmp_path, wt)
    decisions = [run_stop_gate(tmp_path, rd, wt).get("decision") for _ in range(4)]
    assert decisions == ["block", "block", "block", None]


def test_stop_gate_fails_open_without_a_pin(tmp_path):
    wt = make_worktree(tmp_path)
    rd = str(tmp_path / "empty_run")
    os.makedirs(rd)
    assert run_stop_gate(tmp_path, rd, wt) == {}


def test_gate_switch_off_disables_pinning_and_the_stop_hook(tmp_path, monkeypatch):
    wt = make_worktree(tmp_path)
    rd, _ = pin(tmp_path, wt)
    monkeypatch.setenv(acceptance.GATE_ENV, "off")
    assert acceptance.load_pinned(rd) is None
    assert acceptance.pin_acceptance(fixture_ticket(), rd, str(wt),
                                     model_call=lambda t: None) is None
    assert run_stop_gate(tmp_path, rd, wt, env={acceptance.GATE_ENV: "off"}) == {}


def test_guard_blocks_a_write_to_the_pinned_acceptance_file(tmp_path):
    guard = os.path.join(HARNESS_DIR, "guard.sh")
    payload = json.dumps({"tool_name": "Write", "session_id": "t", "cwd": str(tmp_path),
                          "tool_input": {"file_path": str(tmp_path / "acceptance.json"),
                                          "content": "{}"}})
    p = subprocess.run([guard], input=payload, capture_output=True, text=True, timeout=60)
    assert p.returncode != 0, p.stdout
    assert "pinned by fworker" in (p.stdout + p.stderr)


def test_stop_gate_is_wired_into_worker_settings():
    with open(SETTINGS) as f:
        data = json.load(f)
    cmds = [h["command"] for entry in data["hooks"]["Stop"] for h in entry["hooks"]]
    assert any(STOP_GATE.replace(HARNESS_DIR, "/home/lumen/foreman/harness") in c
               for c in cmds), cmds
    deny = data["permissions"]["deny"]
    assert "Edit(**/acceptance.json)" in deny and "Write(**/acceptance.json)" in deny


# ---------------------------------------------------------------------------
# §1d rework
# ---------------------------------------------------------------------------

def test_rework_transition_is_fenced_to_this_attempts_claim(monkeypatch):
    import fworker
    calls = []
    monkeypatch.setattr(fworker, "wq", lambda *a, **k: calls.append(a) or
                        subprocess.CompletedProcess(a, 0, "", ""))

    monkeypatch.setattr(fworker, "wq_show", lambda t: {"claimed_by": "fworker-wq77"})
    assert fworker.wq_rework(77) is True
    assert calls == [("edit", "77", "--status", "rework")]

    calls.clear()
    monkeypatch.setattr(fworker, "wq_show", lambda t: {"claimed_by": "some-other-session"})
    assert fworker.wq_rework(77) is False
    assert calls == []


def test_rework_block_leads_with_repro_first_rules(tmp_path):
    rd = str(tmp_path / "run")
    os.makedirs(rd)
    acceptance.write_rework(rd, 2, [{"id": "AC1", "expected": "exit 0",
                                     "observed": "exit 1", "repro": PROBE_CMD,
                                     "output_tail": "assert False"}])
    block = acceptance.rework_block(rd)
    assert block.startswith("!! PRIOR ATTEMPT REJECTED BY MACHINE REPLAY")
    assert f"repro: {PROBE_CMD}" in block
    assert "do not re-assert them" in block
    assert "assert False" in block


# ---------------------------------------------------------------------------
# the spec's end-to-end test: (a) no evidence, (b) lying evidence, (c) real evidence
# ---------------------------------------------------------------------------

def gated_env(tmp_path, scenario):
    env, _db, _ev, runs_root, _wt_root, _st = make_scratch_env(
        tmp_path, 0, scenario,
        extra={"FOREMAN_EVIDENCE_GATE": "on", "FOREMAN_ACCEPTANCE_DERIVE": "heuristic",
               "FOREMAN_MAX_ATTEMPTS": "1"})
    return env, runs_root


def gated_repo(tmp_path):
    repo = make_scratch_repo(tmp_path)
    with open(os.path.join(repo, "probe_test.sh"), "w") as f:
        f.write("#!/bin/sh\nexit 1\n")  # the deliberately failing criterion
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "probe"], cwd=repo, check=True)
    return repo


def dispatch(tmp_path, scenario):
    repo = gated_repo(tmp_path)
    env, runs_root = gated_env(tmp_path, scenario)
    ticket_id = wq_add(env, "fixture: make the probe pass")
    subprocess.run(["/home/lumen/bin/wq", "edit", str(ticket_id), "--detail",
                     FIXTURE_DETAIL], capture_output=True, text=True, env=env, check=True)
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)
    result = run_fworker(env, ticket_id, repo, scenario, timeout=180)
    rd = os.path.join(runs_root, f"wq{ticket_id}")
    return env, ticket_id, rd, result


def attempt_record(run_dir, n=1):
    with open(os.path.join(run_dir, f"attempt-{n}.json")) as f:
        return json.load(f)


def test_a_no_evidence_never_reaches_done_and_leaves_rework(tmp_path):
    env, ticket_id, rd, result = dispatch(tmp_path, "evidence_missing")
    assert wq_status(env, ticket_id) != "done", result.stdout[-2000:]
    assert os.path.exists(os.path.join(rd, acceptance.ACCEPTANCE_FILE))
    assert os.path.exists(os.path.join(rd, acceptance.REWORK_FILE))
    rec = attempt_record(rd)
    assert rec["evidence_verdict"] == "fail"
    assert rec["classification"] == "FALSE_COMPLETE"


def test_b_evidence_claiming_exit_zero_is_rejected_by_replay(tmp_path):
    env, ticket_id, rd, result = dispatch(tmp_path, "evidence_lie")
    # the worker DID call `wq done` — the gate reverts it: a claim the harness cannot
    # reproduce has no code path to done.
    assert wq_status(env, ticket_id) != "done", result.stdout[-2000:]
    rec = attempt_record(rd)
    assert rec["evidence_verdict"] == "fail"
    assert "AC1" in rec["evidence_failed"]
    with open(os.path.join(rd, acceptance.REWORK_FILE)) as f:
        rework = json.load(f)
    failed = {f_["id"]: f_ for f_ in rework["failed"]}
    assert failed["AC1"]["observed"] == "exit 1" and failed["AC1"]["claimed_exit"] == 0
    assert failed["AC1"]["repro"] == PROBE_CMD


def test_c_true_evidence_is_accepted(tmp_path):
    env, ticket_id, rd, result = dispatch(tmp_path, "evidence_pass")
    assert wq_status(env, ticket_id) == "done", result.stdout[-2000:]
    rec = attempt_record(rd)
    assert rec["evidence_verdict"] == "pass"
    assert not os.path.exists(os.path.join(rd, acceptance.REWORK_FILE))
