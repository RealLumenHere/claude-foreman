"""wq#5143 (wq#5041 gap 2): a ticket with no derivable machine-checkable acceptance
criteria goes to Frank's queue as UNCHECKABLE before a worktree or a token is spent,
instead of dispatching ungated and closing on the worker's own prose.

Before this change: derive() -> criteria [] -> pin_acceptance() None -> verify_done()
vacuous True -> the ticket closed on `wq done` notes alone."""
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import acceptance  # noqa: E402
import failure_record  # noqa: E402
from test_fworker_integration import (  # noqa: E402
    make_scratch_repo, make_scratch_env, wq_add, wq_status, wq_show_field, run_fworker,
    WQ_BIN)
import fworker  # noqa: E402


def _t(archetype, detail="Write up what CodeAF does.", notes=""):
    return {"id": 9, "title": "research ticket", "detail": detail,
            "archetype": archetype, "notes": notes}


# ---------------------------------------------------------------- the pre-change path

def test_before_the_gate_an_empty_doc_verified_vacuously():
    """The defect, pinned: verify() on a zero-criteria doc says ok without looking."""
    doc = acceptance.derive(_t("research-synthesis"))
    assert doc["criteria"] == []
    assert acceptance.verify(doc, None, "/nonexistent")["ok"] is True


# ---------------------------------------------------------------- acceptance.uncheckable

def test_doc_archetype_with_no_command_is_uncheckable():
    t = _t("research-synthesis")
    reason = acceptance.uncheckable(acceptance.derive(t), t)
    assert reason and "research-synthesis" in reason and "not a diff" in reason


def test_rca_with_no_command_is_uncheckable():
    t = _t("rca-investigation", detail="Find out why the timer died.")
    assert acceptance.uncheckable(acceptance.derive(t), t)


def test_a_named_test_command_makes_it_checkable():
    t = _t("research-synthesis", detail="Done when `pytest harness/tests/test_x.py` passes.")
    doc = acceptance.derive(t)
    assert [c["proof_type"] for c in doc["criteria"]] == ["test"]
    assert acceptance.uncheckable(doc, t) is None


def test_diff_archetype_always_has_the_citation_criterion():
    t = _t("standard-engineering", detail="Fix the parser.")
    doc = acceptance.derive(t)
    assert [c["proof_type"] for c in doc["criteria"]] == ["citation"]
    assert acceptance.uncheckable(doc, t) is None


def test_archetypes_checked_elsewhere_are_exempt():
    for arch in ("verify-pass", "review-readonly"):
        t = _t(arch)
        assert acceptance.uncheckable(acceptance.derive(t), t) is None, arch


def test_franks_ack_lifts_the_gate_from_notes_or_detail():
    t = _t("research-synthesis", notes="2026-11-26 [uncheckable-ok] Frank reads it")
    assert acceptance.uncheckable(acceptance.derive(t), t) is None
    t = _t("research-synthesis", detail="Write it up. [uncheckable-ok]")
    assert acceptance.uncheckable(acceptance.derive(t), t) is None


def test_derivation_that_raised_is_uncheckable_even_for_a_diff_archetype():
    reason = acceptance.uncheckable(None, _t("standard-engineering"))
    assert reason and "derivation raised" in reason


# ---------------------------------------------------------------- failure_record route

def test_uncheckable_routes_to_frank_gated():
    assert failure_record.TRIAGE_UNCHECKABLE in failure_record.TRIAGE_VALUES
    assert failure_record.route_for("UNCHECKABLE") == failure_record.ROUTE_FRANK_GATED
    assert "UNCHECKABLE" in failure_record.TRIAGE_NON_COMPETENCE
    # the other classes keep the ordinary block path
    assert failure_record.route_for("UNKNOWN") is None
    assert failure_record.route_for("REAL_BLOCK") is None


def test_uncheckable_record_has_every_v3_key():
    ref = failure_record.build_failure_record(
        ticket=1, attempts=1, classification="CRASH", block_class="INFRA_CRASH",
        events=[], node_map={}, check_worktree=False)
    rec = failure_record.build_uncheckable_record(1, 1, "no criterion", "research")
    assert set(ref) <= set(rec)
    assert rec["schema"] == failure_record.SCHEMA_VERSION == 3
    assert rec["triage"] == "UNCHECKABLE" and rec["route"] == "frank_gated"
    assert failure_record.block_reason_line(rec, "/x/failure.json") == \
        "UNCHECKABLE: no criterion; see /x/failure.json"


def test_uncheckable_prefix_is_a_known_harness_prefix_for_replay():
    assert "UNCHECKABLE:" in failure_record._HARNESS_PREFIXES


# ---------------------------------------------------------------- end to end (real fworker)

def _setup(tmp_path, extra=None):
    repo = make_scratch_repo(tmp_path)
    env, _db, _ev, runs_root, worktrees_root, _st = make_scratch_env(
        tmp_path, 0, "exit_signal_ok", extra=dict({
            "FAKE_CLAUDE_SPAWN_LOG": str(tmp_path / "spawns.log"),
            "FOREMAN_JUDGE_SHADOW": "0",
            "FOREMAN_NO_VERIFY_PASS": "1",
        }, **(extra or {})))
    tid = wq_add(env, "scratch research write-up")
    env["FAKE_CLAUDE_TICKET"] = str(tid)
    return repo, env, tid, runs_root, worktrees_root


def _spawns(tmp_path):
    p = tmp_path / "spawns.log"
    return p.read_text().split() if p.exists() else []


def test_uncheckable_ticket_is_frank_gated_before_any_spawn(tmp_path):
    repo, env, tid, runs_root, worktrees_root = _setup(tmp_path)
    r = run_fworker(env, tid, repo, "exit_signal_ok", archetype="research-synthesis")
    assert r.returncode == fworker.EXIT_FRANK_GATED, r.stdout + r.stderr
    assert "FRANK_GATED" in r.stdout
    assert wq_status(env, tid) in ("frank_gated", "pending_review")
    assert wq_show_field(env, tid, "claimed_by") in ("None", "", None)
    assert _spawns(tmp_path) == []                                   # no token spent
    assert not os.path.isdir(os.path.join(worktrees_root, f"wq{tid}-a1"))  # no worktree
    assert glob.glob(os.path.join(runs_root, f"wq{tid}", "attempt-*.json")) == []
    rec = json.load(open(os.path.join(runs_root, f"wq{tid}", "failure.json")))
    assert rec["triage"] == "UNCHECKABLE" and rec["route"] == "frank_gated"
    shown = subprocess.run([WQ_BIN, "show", str(tid), "--json"], capture_output=True,
                           text=True, env=env).stdout
    assert "no machine-checkable definition of done" in shown
    assert "[uncheckable wq#5143]" in shown


def test_acked_ticket_runs_ungated(tmp_path):
    repo, env, tid, runs_root, _ = _setup(tmp_path)
    p = subprocess.run([WQ_BIN, "note", str(tid), "[uncheckable-ok] Frank reads the doc"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    r = run_fworker(env, tid, repo, "exit_signal_ok", archetype="research-synthesis")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _spawns(tmp_path) == ["fresh"] and wq_status(env, tid) == "done"


def test_shadow_mode_records_and_dispatches_as_before(tmp_path):
    repo, env, tid, _, _ = _setup(tmp_path, {"FOREMAN_UNCHECKABLE_GATE": "shadow"})
    r = run_fworker(env, tid, repo, "exit_signal_ok", archetype="research-synthesis")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "UNCHECKABLE [shadow, wq#5143]" in r.stdout + r.stderr
    assert _spawns(tmp_path) == ["fresh"] and wq_status(env, tid) == "done"


def test_checkable_ticket_is_untouched(tmp_path):
    repo, env, tid, _, _ = _setup(tmp_path)
    r = run_fworker(env, tid, repo, "exit_signal_ok", archetype="mechanical-edit")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "UNCHECKABLE" not in r.stdout + r.stderr
    assert _spawns(tmp_path) == ["fresh"] and wq_status(env, tid) == "done"
