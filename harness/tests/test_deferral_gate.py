"""wq#2597 — a deferred AC bullet gets a ticket, or it does not get deferred.

The regression under test is wq#2583's real closing notes: they deferred the queue re-route
in plain language, cited no ticket for it, and closed `done`. That exact string must now
produce a follow-up row. Nothing here touches the live queue: `run_wq` is a recorder.
"""
import os
import sys
import types

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import deferral_gate  # noqa: E402

WQ2583_NOTES = (
    "PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/967 — local-worker dropped from "
    "mechanical-edit and probe-build (research/models.yaml:330-331), router tests updated and "
    "passing. Queue re-route of blocked tickets + SPAWN_LOOP tickets left as follow-up (out of "
    "scope for a worktree-isolated code PR).")


def recorder(returncode=0, stdout="added task 2601 [build]", stderr=""):
    calls = []

    def run_wq(argv):
        calls.append(list(argv))
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    return run_wq, calls


def test_the_wq2583_notes_are_caught():
    assert "left as follow-up" in deferral_gate.scan(WQ2583_NOTES, 2583)


def test_citing_a_followup_ticket_satisfies_the_rule():
    """The rule is 'name the ticket', not 'never use the word'."""
    notes = WQ2583_NOTES + " Re-route sweep filed as wq#2601."
    assert deferral_gate.scan(notes, 2583) is None


def test_the_tickets_own_id_does_not_count_as_a_citation():
    notes = "wq#2583: sweep left as follow-up, see wq#2583's AC."
    assert deferral_gate.scan(notes, 2583) is not None


@pytest.mark.parametrize("notes", [
    "PR #967 landed; research/models.yaml:330-331 + 11 router tests pass.",
    "Closed with evidence: harness/tests/test_router.py::test_local_rung_dropped passes.",
    "Reverted wq#2101's change; the follow-on work is tracked on wq#2444.",
])
def test_ordinary_closures_are_untouched(notes):
    assert deferral_gate.scan(notes, 2583) is None


@pytest.mark.parametrize("phrase", [
    "the second half is deferred to a later pass",
    "TODO: sweep the remaining rows",
    "the SPAWN_LOOP subset is still unrun",
    "AC part 3 not done — no capacity this run",
    "left for later",
    "out of scope for a worktree-isolated code PR",
])
def test_deferral_vocabulary(phrase):
    assert deferral_gate.scan(f"PR #1 landed. {phrase}.", 1) is not None


def test_enforce_files_the_followup_and_cites_it():
    run_wq, calls = recorder()
    out = deferral_gate.enforce(2583, WQ2583_NOTES, run_wq, lane="build", pri=1)
    assert len(calls) == 1
    argv = calls[0]
    assert argv[0] == "add"
    assert "--deps" in argv and argv[argv.index("--deps") + 1] == "2583"
    assert argv[argv.index("--lane") + 1] == "build"
    assert argv[argv.index("--pri") + 1] == "1"
    # wq#1676: an untagged row is refused outright, so the filing must carry a probe route
    assert "--accept-probe" in argv
    assert "wq#2601" in out and deferral_gate.MARKER in out
    # the original notes survive verbatim — the gate annotates, it never rewrites
    assert WQ2583_NOTES.rstrip() in out


def test_parent_probe_is_reused_when_the_parent_has_one():
    run_wq, calls = recorder()
    deferral_gate.enforce(2583, WQ2583_NOTES, run_wq, probe="sqlite3 ... == 0")
    assert calls[0][calls[0].index("--liveness") + 1] == "sqlite3 ... == 0"


def test_a_refused_parent_probe_falls_back_to_derivation():
    """A stale probe on the parent must not cost the follow-up row."""
    calls = []
    outcomes = [types.SimpleNamespace(returncode=1, stdout="", stderr="REFUSED: liveness"),
                types.SimpleNamespace(returncode=0, stdout="added task 2602 [build]", stderr="")]

    def run_wq(argv):
        calls.append(list(argv))
        return outcomes[len(calls) - 1]

    out = deferral_gate.enforce(2583, WQ2583_NOTES, run_wq, probe="bad probe")
    assert "--liveness" in calls[0] and "--accept-probe" in calls[1]
    assert "wq#2602" in out


def test_a_failed_filing_is_recorded_in_the_notes_not_swallowed():
    """The failure mode this module exists for is silence, so the unfiled case has to be
    legible on the row itself."""
    run_wq, _ = recorder(returncode=2, stdout="", stderr="REFUSED: lane has no LANE_REPO row")
    out = deferral_gate.enforce(2583, WQ2583_NOTES, run_wq)
    assert "DEFERRAL-UNFILED" in out and "LANE_REPO" in out


def test_no_deferral_means_no_wq_call_at_all():
    run_wq, calls = recorder()
    notes = "PR #967 landed; 111 router tests pass."
    assert deferral_gate.enforce(2583, notes, run_wq) == notes
    assert calls == []


def test_parse_new_id_matches_wq_add_output():
    assert deferral_gate.parse_new_id("added task 2601 [build]") == 2601
    assert deferral_gate.parse_new_id("REFUSED: something") is None
