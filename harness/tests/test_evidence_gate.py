"""ISSUE-SOLUTIONS-fable.md §1 / ISSUE-LOG #17 — evidence-gated done.

The spec's proving test, end to end on a real git worktree with a real failing test:
  (a) worker emits no evidence            -> Stop hook blocks; verify_done routes to
                                             rework; `done` is never reached
  (b) worker claims exit 0 for a test that
      actually exits 1                    -> harness replay observes exit 1, rejects
  (c) the test is fixed and evidence redone-> accepted

Zero quota: no `claude` child is ever spawned. wq is monkeypatched, so no live ticket is
touched either.
"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import acceptance  # noqa: E402
import fworker  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "stop_gate", os.path.join(HARNESS_DIR, "hooks", "stop_gate.py"))
stop_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stop_gate)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo)] + list(args),
                          capture_output=True, text=True, check=True)


@pytest.fixture
def worktree(tmp_path):
    """A branch with one committed change, and a `probe.py` whose exit code the test
    controls — the stand-in for a criterion command that really does fail."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", str(repo)],
                   check=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "base.txt").write_text("pre-existing, untouched by this branch\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "feat/evidence")
    (repo / "impl.py").write_text("".join(f"line {i}\n" for i in range(1, 21)))
    (repo / "probe.py").write_text("import sys\nsys.exit(1)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "work")
    return repo


def _acceptance_doc():
    return {"ticket": "wq#4242", "derived_by": "deterministic", "notes": [],
            "criteria": [
                {"id": "AC1", "text": "the probe passes", "proof_type": "test",
                 "cmd": "python3 probe.py", "expect_exit": 0},
                {"id": "AC2", "text": "cite the implementation", "proof_type": "citation"}]}


@pytest.fixture
def gated(worktree, tmp_path, monkeypatch):
    """acceptance.json pinned for wq#4242, with fworker's wq calls recorded, not made."""
    runs = tmp_path / "runs"
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(runs))
    calls = {"status": "done", "attempt": "1", "log": []}
    monkeypatch.setattr(fworker, "wq_show",
                        lambda tid: {"status": calls["status"], "attempt": calls["attempt"]})
    monkeypatch.setattr(fworker, "wq_release",
                        lambda tid: calls["log"].append(("release", tid)))
    monkeypatch.setattr(fworker, "wq_claim",
                        lambda tid: (calls["log"].append(("claim", tid)), (True, ""))[1])
    monkeypatch.setattr(fworker, "wq_block",
                        lambda tid, why: calls["log"].append(("block", tid, why)))
    monkeypatch.setattr(fworker, "wq_done",
                        lambda tid, notes: calls["log"].append(("done", tid, notes)))
    monkeypatch.setattr(fworker, "ntfy", lambda *a, **k: None)
    doc = fworker.pin_acceptance(4242, {"title": "t", "detail": "d"}, str(worktree), 1)
    assert doc is None or doc  # pin_acceptance returns the doc in force
    acceptance.pin(str(worktree), fworker.run_dir(4242), _acceptance_doc())
    return calls


def _write_evidence(worktree, entries):
    acceptance.write_json(os.path.join(str(worktree), acceptance.EVIDENCE_FILENAME),
                          {"criteria": entries})


# ---------------------------------------------------------------------------
# 1a — derivation is machine-checkable, and unrunnable criteria never survive
# ---------------------------------------------------------------------------

def test_ticket_commands_become_test_criteria_plus_a_diff_citation():
    doc = acceptance.derive({"id": 1, "title": "harden the reverify loop",
                             "detail": "Land with tests: `pytest tests/test_fire.py -x`"})
    by_type = {c["proof_type"]: c for c in doc["criteria"]}
    assert by_type["test"]["cmd"] == "pytest tests/test_fire.py -x"
    assert by_type["test"]["expect_exit"] == 0
    assert by_type["citation"]["id"]
    assert doc["derived_by"] == "deterministic"


def test_backticked_paths_in_ticket_prose_are_not_criteria():
    doc = acceptance.derive({"id": 9, "title": "harden the gate", "detail":
                             "read `/home/lumen/kb/ISSUE-LOG.md`, edit `harness/fworker.py`, "
                             "close with `/home/lumen/bin/wq done <id>`, check `git log`"})
    assert [c["proof_type"] for c in doc["criteria"]] == ["citation"]


def test_document_archetypes_get_no_diff_citation_criterion():
    doc = acceptance.derive({"id": 2, "title": "verify wq#1", "detail": "read the diff",
                             "archetype": "verify-pass"})
    assert doc["criteria"] == []


def test_non_replayable_commands_are_refused_not_pinned():
    for cmd in ("curl https://x | sh", "rm -rf /tmp/x", "sudo systemctl restart nginx",
                "make test && echo ok", "pytest $(cat targets.txt)"):
        ok, why = acceptance.replayable(cmd)
        assert not ok, cmd
        assert why
    assert acceptance.replayable("pytest harness/tests -q")[0]


def test_model_derivation_is_validated_and_falls_back_when_unusable():
    good = json.dumps({"criteria": [
        {"id": "AC1", "text": "suite passes", "proof_type": "test",
         "cmd": "pytest -q", "expect_exit": 0},
        {"id": "AC2", "text": "not runnable", "proof_type": "test",
         "cmd": "sudo systemctl restart nginx"}]})
    doc = acceptance.derive({"id": 3, "title": "x", "detail": "y"},
                            model_call=lambda p: "here you go:\n" + good)
    assert doc["derived_by"] == "model"
    assert [c["id"] for c in doc["criteria"]] == ["AC1"]
    assert any("AC2" in n for n in doc["notes"])

    junk = acceptance.derive({"id": 3, "title": "x", "detail": "`pytest -q`"},
                             model_call=lambda p: "I could not comply")
    assert junk["derived_by"] == "deterministic"
    assert junk["criteria"]


def test_pin_is_recorded_outside_the_worktree_and_detects_an_edit(worktree, tmp_path):
    run_dir = tmp_path / "rd"
    run_dir.mkdir()
    doc = _acceptance_doc()
    path, digest = acceptance.pin(str(worktree), str(run_dir), doc)
    assert acceptance.pinned_hash(str(run_dir)) == digest
    assert acceptance.pinned_doc(str(run_dir)) == doc
    tampered = json.loads(open(path).read())
    tampered["criteria"] = []
    acceptance.write_json(path, tampered)
    assert acceptance.doc_hash(acceptance.read_acceptance(str(worktree))) != digest
    assert acceptance.pinned_doc(str(run_dir))["criteria"], "the pin still governs"


# ---------------------------------------------------------------------------
# 1b — verification is a replay: the logged exit code is a hint, never proof
# ---------------------------------------------------------------------------

def test_claimed_exit_zero_loses_to_the_harness_replay(worktree):
    result = acceptance.verify(
        _acceptance_doc(),
        {"criteria": [{"id": "AC1", "cmd_run": "python3 probe.py", "exit_code": 0},
                      {"id": "AC2", "citation": "impl.py:3"}]},
        str(worktree), base_ref="main")
    assert not result["ok"]
    failed = {f["id"]: f for f in result["failed"]}
    assert set(failed) == {"AC1"}
    assert failed["AC1"]["claimed"] == 0
    assert failed["AC1"]["observed"] == "exit 1"
    assert failed["AC1"]["repro"] == "python3 probe.py"


def test_a_passing_probe_with_a_real_citation_is_accepted(worktree):
    (worktree / "probe.py").write_text("import sys\nsys.exit(0)\n")
    result = acceptance.verify(
        _acceptance_doc(),
        {"criteria": [{"id": "AC1", "cmd_run": "python3 probe.py", "exit_code": 0},
                      {"id": "AC2", "citation": "impl.py:3-8"}]},
        str(worktree), base_ref="main")
    assert result["ok"], result["failed"]


def test_missing_criteria_are_failures_not_omissions(worktree):
    result = acceptance.verify(_acceptance_doc(), {"criteria": []}, str(worktree),
                               base_ref="main")
    assert {f["id"] for f in result["failed"]} == {"AC1", "AC2"}
    assert all(f["observed"] == "missing" for f in result["failed"])


@pytest.mark.parametrize("citation,why", [
    ("nope.py:3", "does not exist"),
    ("impl.py:999", "cited"),
    ("base.txt:1", "not touched by this branch"),
    ("impl.py", "not file:line"),
])
def test_citations_are_checked_against_the_file_and_the_diff(worktree, citation, why):
    (worktree / "probe.py").write_text("import sys\nsys.exit(0)\n")
    result = acceptance.verify(
        _acceptance_doc(),
        {"criteria": [{"id": "AC1", "cmd_run": "python3 probe.py", "exit_code": 0},
                      {"id": "AC2", "citation": citation}]},
        str(worktree), base_ref="main")
    assert not result["ok"]
    assert why in [f for f in result["failed"] if f["id"] == "AC2"][0]["observed"]


def test_uncommitted_work_still_counts_as_touched(worktree):
    (worktree / "fresh.py").write_text("x = 1\ny = 2\n")
    assert "fresh.py" in acceptance.changed_files(str(worktree), base_ref="main")


def test_replay_writes_the_merged_output_log(worktree, tmp_path):
    (worktree / "probe.py").write_text(
        "import sys\nprint('to stdout')\nprint('to stderr', file=sys.stderr)\nsys.exit(1)\n")
    ev_dir = tmp_path / "ev"
    result = acceptance.verify(_acceptance_doc(),
                               {"criteria": [{"id": "AC1", "cmd_run": "python3 probe.py",
                                              "exit_code": 0}]},
                               str(worktree), base_ref="main", evidence_dir=str(ev_dir))
    log = (ev_dir / "replay-AC1.log").read_text()
    assert "to stdout" in log and "to stderr" in log and "[exit 1]" in log
    assert "to stderr" in [f for f in result["failed"] if f["id"] == "AC1"][0]["output_tail"]


def test_no_criteria_means_the_gate_is_vacuous_not_failing(worktree):
    assert acceptance.verify({"criteria": []}, None, str(worktree))["ok"]


# ---------------------------------------------------------------------------
# 1c — the Stop hook
# ---------------------------------------------------------------------------

def test_stop_hook_ignores_a_session_with_no_pinned_criteria(worktree, monkeypatch):
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", str(worktree))
    assert stop_gate.decide({}) == (stop_gate.ALLOW, "")


def test_stop_hook_blocks_a_stop_with_no_evidence(worktree, tmp_path, monkeypatch):
    acceptance.pin(str(worktree), str(tmp_path), _acceptance_doc())
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", str(worktree))
    monkeypatch.setenv("FOREMAN_STOP_GATE_STATE", str(tmp_path / "stop-gate.json"))
    monkeypatch.setattr(stop_gate, "_ticket_is_done", lambda tid: False)
    code, msg = stop_gate.decide({"session_id": "s1"})
    assert code == stop_gate.BLOCK
    assert "AC1" in msg and "AC2" in msg and "python3 probe.py" in msg


def test_stop_hook_allows_once_every_criterion_has_an_entry(worktree, tmp_path, monkeypatch):
    acceptance.pin(str(worktree), str(tmp_path), _acceptance_doc())
    _write_evidence(worktree, [{"id": "AC1", "cmd_run": "python3 probe.py", "exit_code": 1},
                               {"id": "AC2", "citation": "impl.py:3"}])
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", str(worktree))
    monkeypatch.setattr(stop_gate, "_ticket_is_done", lambda tid: False)
    # note: exit_code 1 still ALLOWS the stop — the hook checks completeness, the
    # harness-side replay checks truth. The hook must not become a second, weaker judge.
    assert stop_gate.decide({"session_id": "s1"}) == (stop_gate.ALLOW, "")


def test_stop_hook_names_the_specific_gap(worktree, tmp_path, monkeypatch):
    acceptance.pin(str(worktree), str(tmp_path), _acceptance_doc())
    _write_evidence(worktree, [{"id": "AC1", "cmd_run": "python3 probe.py", "exit_code": 0}])
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", str(worktree))
    monkeypatch.setenv("FOREMAN_STOP_GATE_STATE", str(tmp_path / "stop-gate.json"))
    monkeypatch.setattr(stop_gate, "_ticket_is_done", lambda tid: False)
    code, msg = stop_gate.decide({"session_id": "s1"})
    assert code == stop_gate.BLOCK
    assert "AC2" in msg and "AC1" not in msg.split("Unproven criteria:")[1].split("What each")[0]


def test_stop_hook_block_count_is_capped(worktree, tmp_path, monkeypatch):
    acceptance.pin(str(worktree), str(tmp_path), _acceptance_doc())
    monkeypatch.setenv("FOREMAN_WORKTREE_ROOT", str(worktree))
    monkeypatch.setenv("FOREMAN_STOP_GATE_STATE", str(tmp_path / "stop-gate.json"))
    monkeypatch.setattr(stop_gate, "_ticket_is_done", lambda tid: False)
    codes = [stop_gate.decide({"session_id": "s1"})[0] for _ in range(5)]
    assert codes == [stop_gate.BLOCK] * stop_gate.MAX_BLOCKS + [stop_gate.ALLOW] * 2


def test_stop_hook_honours_the_hook_contract_as_a_real_subprocess(worktree, tmp_path):
    """Exit 2 + reason on stderr is the whole contract Claude Code acts on — assert it
    on the actual process, not just on decide()."""
    acceptance.pin(str(worktree), str(tmp_path), _acceptance_doc())
    env = dict(os.environ, FOREMAN_WORKTREE_ROOT=str(worktree),
               FOREMAN_STOP_GATE_STATE=str(tmp_path / "stop-gate.json"),
               FOREMAN_WQ_BIN="/bin/false", FOREMAN_TICKET_ID="4242")
    p = subprocess.run([sys.executable, os.path.join(HARNESS_DIR, "hooks", "stop_gate.py")],
                       input=json.dumps({"session_id": "s1", "cwd": str(worktree)}),
                       capture_output=True, text=True, env=env)
    assert p.returncode == stop_gate.BLOCK
    assert "STOP BLOCKED (1/3)" in p.stderr and "AC1" in p.stderr

    _write_evidence(worktree, [{"id": "AC1", "cmd_run": "python3 probe.py", "exit_code": 1},
                               {"id": "AC2", "citation": "impl.py:3"}])
    p2 = subprocess.run([sys.executable, os.path.join(HARNESS_DIR, "hooks", "stop_gate.py")],
                        input=json.dumps({"session_id": "s1", "cwd": str(worktree)}),
                        capture_output=True, text=True, env=env)
    assert p2.returncode == stop_gate.ALLOW and p2.stderr == ""


def test_stop_hook_fails_open_on_a_broken_input(monkeypatch):
    monkeypatch.setattr(sys, "stdin", type("S", (), {"read": staticmethod(lambda: "{{{")})())
    assert stop_gate.main() == stop_gate.ALLOW


# ---------------------------------------------------------------------------
# 1b/1d — fworker's transition: the spec's (a)/(b)/(c), on the real code path
# ---------------------------------------------------------------------------

def test_a_no_evidence_never_reaches_done_and_files_rework(worktree, gated):
    ok, escalated = fworker.verify_done(4242, str(worktree), 1, base_ref="main")
    assert (ok, escalated) == (False, False)
    rework = acceptance.read_json(os.path.join(fworker.run_dir(4242),
                                                acceptance.REWORK_FILENAME))
    assert {f["id"] for f in rework["failed"]} == {"AC1", "AC2"}
    assert rework["attempt"] == 1
    # the premature self-`done` is undone, and re-claimed so the row is not left loose
    assert [c[0] for c in gated["log"]] == ["release", "claim"]
    assert ("done", 4242) not in [(c[0], c[1]) for c in gated["log"]]
    # and a landed-looking artifact cannot resurrect it
    assert fworker.evidence_rejected(4242)
    assert fworker.check_artifacts_done(4242, str(worktree)) is False


def test_b_a_claimed_pass_is_rejected_by_the_harness_replay(worktree, gated):
    _write_evidence(worktree, [{"id": "AC1", "cmd_run": "python3 probe.py", "exit_code": 0},
                               {"id": "AC2", "citation": "impl.py:3"}])
    ok, escalated = fworker.verify_done(4242, str(worktree), 1, base_ref="main")
    assert (ok, escalated) == (False, False)
    rework = acceptance.read_json(os.path.join(fworker.run_dir(4242),
                                                acceptance.REWORK_FILENAME))
    assert [f["id"] for f in rework["failed"]] == ["AC1"]
    assert rework["failed"][0]["claimed"] == 0
    assert rework["failed"][0]["observed"] == "exit 1"
    # the rejected attempt's own evidence is kept for the next attempt to read
    assert os.path.isfile(os.path.join(rework["prior_evidence"],
                                        acceptance.EVIDENCE_FILENAME))


def test_c_fixing_the_failure_is_accepted_and_clears_the_rejection(worktree, gated):
    _write_evidence(worktree, [{"id": "AC1", "cmd_run": "python3 probe.py", "exit_code": 0},
                               {"id": "AC2", "citation": "impl.py:3"}])
    assert fworker.verify_done(4242, str(worktree), 1, base_ref="main") == (False, False)
    (worktree / "probe.py").write_text("import sys\nsys.exit(0)\n")
    assert fworker.verify_done(4242, str(worktree), 2, base_ref="main") == (True, False)
    assert not fworker.evidence_rejected(4242)


def test_editing_the_pinned_acceptance_is_itself_a_failure(worktree, gated):
    acceptance.write_json(os.path.join(str(worktree), acceptance.ACCEPTANCE_FILENAME),
                          {"ticket": "wq#4242", "criteria": []})
    _write_evidence(worktree, [])
    ok, _ = fworker.verify_done(4242, str(worktree), 1, base_ref="main")
    assert not ok
    rework = acceptance.read_json(os.path.join(fworker.run_dir(4242),
                                                acceptance.REWORK_FILENAME))
    assert rework["failed"][0]["id"] == "PIN"


def test_the_attempt_ceiling_escalates_instead_of_dispatching_again(worktree, gated):
    gated["attempt"] = "3"
    ok, escalated = fworker.verify_done(4242, str(worktree), 3, base_ref="main")
    assert (ok, escalated) == (False, True)
    blocks = [c for c in gated["log"] if c[0] == "block"]
    assert len(blocks) == 1 and "evidence gate" in blocks[0][2]
    assert "release" not in [c[0] for c in gated["log"]], "escalated, not re-dispatched"


def test_the_gate_can_be_switched_off_without_a_code_change(worktree, gated, monkeypatch):
    monkeypatch.setenv("FOREMAN_EVIDENCE_GATE", "0")
    assert fworker.verify_done(4242, str(worktree), 1, base_ref="main") == (True, False)
    assert not os.path.isfile(os.path.join(fworker.run_dir(4242),
                                            acceptance.REWORK_FILENAME))


# ---------------------------------------------------------------------------
# 1a/1d — what the worker actually reads
# ---------------------------------------------------------------------------

def test_the_prompt_carries_the_contract_and_puts_rework_first(worktree, tmp_path,
                                                                monkeypatch):
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path / "runs"))
    monkeypatch.setattr(fworker, "distilled_memory_block", lambda t: "")
    doc = _acceptance_doc()
    result = acceptance.verify(doc, {"criteria": []}, str(worktree), base_ref="main")
    rework = acceptance.rework_doc(4242, 1, result, prior_evidence_dir="/tmp/ev")

    plain = fworker.load_worker_prompt(4242, {"title": "t", "detail": "d"}, str(worktree),
                                        acceptance_doc=doc)
    assert "{{ACCEPTANCE_CONTRACT}}" not in plain and "{{REWORK}}" not in plain
    assert "DONE IS A REPLAY, NOT A CLAIM" in plain
    assert "python3 probe.py" in plain and "evidence.json" in plain

    reworked = fworker.load_worker_prompt(4242, {"title": "t", "detail": "d"},
                                           str(worktree), acceptance_doc=doc, rework=rework)
    assert reworked.startswith("REWORK — YOUR PREVIOUS ATTEMPT'S EVIDENCE FAILED")
    for rule in ("Run each one and confirm", "rejected by machine replay",
                 "same pinned acceptance.json still governs"):
        assert rule in reworked
    assert "repro: python3 probe.py" in reworked


def test_an_ungated_ticket_renders_exactly_as_before(worktree, tmp_path, monkeypatch):
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path / "runs"))
    monkeypatch.setattr(fworker, "distilled_memory_block", lambda t: "")
    p = fworker.load_worker_prompt(4242, {"title": "t", "detail": "d"}, str(worktree))
    assert p.startswith("You are a Lumen fleet BUILD worker")
    assert "DONE IS A REPLAY" not in p


def test_settings_wire_the_stop_gate_and_deny_editing_the_pin():
    with open(os.path.join(HARNESS_DIR, "worker-settings.json")) as f:
        settings = json.load(f)
    stops = [h["command"] for entry in settings["hooks"]["Stop"] for h in entry["hooks"]]
    assert any("stop_gate.py" in c for c in stops)
    for pack in (settings["permissions"]["deny"],
                 json.load(open(os.path.join(HARNESS_DIR, "permission-profiles",
                                             "base.json")))["deny"]):
        assert "Write(**/acceptance.json)" in pack and "Edit(**/acceptance.json)" in pack
