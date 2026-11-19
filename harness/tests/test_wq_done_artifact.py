"""wq#1925 (adjudicating wq#1924's CONTRADICT of wq#1923/PR#588) — `done` may only be
closed as "no code artifact" by a caller that knows it produced none.

The bug: wq_done()'s wq#759 evidence-gate retry fired on refusal TEXT alone, so
handle_resumable_failure() — which is only reached after check_artifacts_done() found a
real commit — closed its ticket with --no-artifact "no code artifact", a false label on a
ticket that did land code, in the same audit bucket (`wq closure-quality`) wq#759 uses to
track closure quality.

Zero quota, no live ticket: `wq` is faked for the unit tests, and the one end-to-end test
runs the real binary against a throwaway WQ_DB.
"""
import os
import subprocess
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fworker  # noqa: E402
from classifier import INDETERMINATE  # noqa: E402

# verbatim from /home/lumen/bin/wq:2536 (cmd_done) — if that text ever drifts, the
# refusal-match tests below are what notices.
GATE_REFUSAL = ("REFUSED: wq#759 evidence gate — task 4242 not closed. `done` requires "
                "--notes naming a verifiable artifact (an existing file path, a PR/commit "
                "URL, or a commit sha that resolves in a repo on this box) or an explicit "
                "--no-artifact \"<reason>\" recorded on the ticket.")
# wq:2523 (cmd_done deliverable gate, exit 7) — a DIFFERENT refusal, never retryable
DELIVERABLE_REFUSAL = ("REFUSED: wq#1673/wq#1702 deliverable gate — task 4242 not closed. "
                       "Its named output is not on main.")


def _fake_wq(monkeypatch, first_rc, first_out, second_rc=0):
    """Record every `wq` argv; answer the first `done` with a refusal, the retry with
    second_rc."""
    seen = []

    def fake(*args, check=False):
        seen.append(list(args))
        if len(seen) == 1:
            return types.SimpleNamespace(returncode=first_rc, stdout="", stderr=first_out)
        return types.SimpleNamespace(returncode=second_rc, stdout="", stderr="")

    monkeypatch.setattr(fworker, "wq", fake)
    monkeypatch.setattr(fworker, "log", lambda *a, **k: None)
    return seen


def test_evidence_gate_refusal_without_a_caller_reason_raises_instead_of_mislabelling(monkeypatch):
    seen = _fake_wq(monkeypatch, 2, GATE_REFUSAL)
    with pytest.raises(RuntimeError, match="refused"):
        fworker.wq_done(4242, "fworker attempt 2: verified via artifacts")
    assert len(seen) == 1, "no --no-artifact retry may be invented for this caller"
    assert "--no-artifact" not in seen[0]


def test_evidence_gate_refusal_retries_once_with_the_callers_own_reason(monkeypatch):
    seen = _fake_wq(monkeypatch, 2, GATE_REFUSAL)
    fworker.wq_done(4242, "probe cleared", no_artifact_reason="no code change was applied")
    assert len(seen) == 2
    assert seen[1][-2:] == ["--no-artifact", "no code change was applied"]


def test_a_retry_that_is_also_refused_still_raises(monkeypatch):
    _fake_wq(monkeypatch, 2, GATE_REFUSAL, second_rc=3)
    with pytest.raises(RuntimeError):
        fworker.wq_done(4242, "probe cleared", no_artifact_reason="nothing to cite")


def test_a_non_evidence_gate_refusal_is_never_retried(monkeypatch):
    seen = _fake_wq(monkeypatch, 7, DELIVERABLE_REFUSAL)
    with pytest.raises(RuntimeError):
        fworker.wq_done(4242, "probe cleared", no_artifact_reason="no code change was applied")
    assert len(seen) == 1


def test_check_artifacts_done_hands_back_the_commit_it_found(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", str(repo)], check=True)
    for cfg in (("user.email", "t@example.com"), ("user.name", "test")):
        subprocess.run(["git", "-C", str(repo), "config", *cfg], check=True)
    (repo / "f.txt").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "fix(x): wq#4242 land it"],
                   check=True)
    # no remote: point origin/main at the local commit, the same ref the check reads
    subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", "HEAD"],
                   check=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()

    # wq#5030: wq#4242 is a real ticket; its live run dir carries an acceptance/evidence doc whose
    # citations do not resolve in THIS scratch repo. Read run dirs from an empty root instead.
    import acceptance
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path / "runs"))
    monkeypatch.setattr(acceptance, "RUNS_ROOT", str(tmp_path / "runs"))
    monkeypatch.setattr(fworker, "evidence_rejected", lambda tid: False)
    monkeypatch.setattr(fworker, "wq_show", lambda tid: {"status": "in_progress"})
    assert fworker.check_artifacts_done(4242, str(repo)) == (True, head)

    monkeypatch.setattr(fworker, "wq_show", lambda tid: {"status": "done"})
    assert fworker.check_artifacts_done(4242, str(repo)) == (True, None)


def test_handle_resumable_failure_cites_the_landed_commit_it_verified(monkeypatch):
    sha = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
    done = []
    monkeypatch.setattr(fworker, "check_artifacts_done", lambda tid, wt: (True, sha))
    monkeypatch.setattr(fworker, "wq_done",
                        lambda tid, notes, no_artifact_reason=None:
                        done.append((tid, notes, no_artifact_reason)))
    monkeypatch.setattr(fworker, "file_verify_pass", lambda tid, args: None)
    monkeypatch.setattr(fworker, "log", lambda *a, **k: None)
    state = types.SimpleNamespace(session_id="0b1e2c34-aaaa-bbbb-cccc-ddddeeeeffff")

    rc = fworker.handle_resumable_failure(types.SimpleNamespace(ticket=4242), 4242, 2,
                                          INDETERMINATE, state, [], "/nonexistent")

    assert rc == fworker.EXIT_OK
    assert len(done) == 1
    assert sha in done[0][1], "the artifact that proved the landing must be in the notes"
    assert done[0][2] is None, "this caller may never claim artifact-lessness"


def test_an_already_done_ticket_is_not_closed_again(monkeypatch):
    monkeypatch.setattr(fworker, "check_artifacts_done", lambda tid, wt: (True, None))
    monkeypatch.setattr(fworker, "wq_done",
                        lambda *a, **k: pytest.fail("already done — nothing to close"))
    monkeypatch.setattr(fworker, "file_verify_pass", lambda tid, args: None)
    monkeypatch.setattr(fworker, "log", lambda *a, **k: None)
    state = types.SimpleNamespace(session_id="s")
    assert fworker.handle_resumable_failure(types.SimpleNamespace(ticket=4242), 4242, 2,
                                            INDETERMINATE, state, [], "/nonexistent") \
        == fworker.EXIT_OK


# ---------------------------------------------------------------------------
# end to end against the real gate — the claim under adjudication, live
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not os.path.exists(fworker.WQ_BIN), reason="wq not on this box")
def test_real_wq_refuses_the_prose_notes_and_accepts_the_commit_citation(tmp_path):
    env = dict(os.environ, WQ_DB=str(tmp_path / "q.db"))

    def wq(*args):
        return subprocess.run([fworker.WQ_BIN, *args], capture_output=True, text=True, env=env)

    sha = subprocess.run(["git", "-C", os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    session = "0b1e2c34-aaaa-bbbb-cccc-ddddeeeeffff"
    prose = f"fworker attempt 2: verified via artifacts after INDETERMINATE (session {session})"

    for n in (1, 2):
        assert wq("add", "--title", f"gate probe {n}", "--lane", "build").returncode == 0

    refused = wq("done", "1", "--notes", prose)
    assert refused.returncode == 2
    out = refused.stdout + " " + refused.stderr
    # exactly fworker.wq_done()'s match condition: the retry WOULD fire here, which is
    # why it must not fire without the caller's own reason
    assert "759" in out or "evidence gate" in out.lower() or "verifiable artifact" in out.lower()

    accepted = wq("done", "2", "--notes", f"{prose} - landed commit {sha}")
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
