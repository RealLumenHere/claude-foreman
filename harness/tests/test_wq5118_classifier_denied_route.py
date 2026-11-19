"""wq#5118 -- an attempt that dies on a Claude Code permission decision (auto-mode
classifier / hard denial) goes to Frank's decision queue instead of being retried.
wq#5002 ran two attempts into the same refusal 5x each and was blocked UNKNOWN /
INFRA_CRASH.

Same shape as test_local_rung_escalation.py: this is about the route handle_failure
picks, so the wq calls are captured and record_terminal_failure runs for real against
a tmp run dir (the triage is the thing under test; its sidecars are stubbed).
"""
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import pytest  # noqa: E402

import fworker  # noqa: E402
import failure_record  # noqa: E402

DENIAL = ("Permission for this action was denied by the Claude Code auto mode "
          "classifier. Reason: Unauthorized Access. If you have other tasks that don't "
          "depend on this action, continue working on those. IMPORTANT: ... do not "
          "attempt to bypass the intent behind this denial. ... Let the user decide how "
          "to proceed.")
CMD = "grep -n litellm /home/lumen/foreman/harness/hallucination_guard.py"


def denial_events(times=5, text=DENIAL):
    ev = []
    for i in range(times):
        ev.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": CMD}, "id": f"t{i}"}]}})
        ev.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "is_error": True,
             "content": text}]}})
    return ev


class _Args:
    model = "claude-opus-5-5"
    repo = "/nowhere"
    archetype = "research-synthesis"


class _P:
    def __init__(self, returncode=0):
        self.returncode, self.stdout, self.stderr = returncode, "", ""


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A one-row fake wq: release -> open/unclaimed, edit --status sets it, decide ->
    pending_review. `fail` names wq verbs that exit 1 without changing the row."""
    st = {"row": {"id": 5002, "status": "claimed", "claimed_by": "fworker-wq5002",
                  "lane": "research", "pri": 2, "title": "t", "detail": "", "needs": ""},
          "calls": [], "block": [], "ntfy": [], "fail": set(), "recipe": []}

    def wq(*args, check=False):
        st["calls"].append(args)
        if args[0] in st["fail"]:
            return _P(1)
        if args[0] == "edit" and "--status" in args:
            st["row"]["status"] = args[args.index("--status") + 1]
        elif args[0] == "decide":
            st["row"]["status"] = "pending_review"
        return _P()

    def release(tid):
        st["calls"].append(("release", str(tid)))
        st["row"].update(status="open", claimed_by=None)

    monkeypatch.setattr(fworker, "wq", wq)
    monkeypatch.setattr(fworker, "wq_release", release)
    monkeypatch.setattr(fworker, "wq_show", lambda t: dict(st["row"]))
    monkeypatch.setattr(fworker, "wq_block", lambda t, r: st["block"].append((t, r)))
    monkeypatch.setattr(fworker, "ntfy", lambda *a, **k: st["ntfy"].append(a))
    monkeypatch.setattr(fworker, "write_prior_attempt_notes", lambda *a, **k: None)
    monkeypatch.setattr(fworker, "record_recipe_outcome",
                        lambda t, a, success, triage=None: st["recipe"].append(triage))
    monkeypatch.setattr(fworker, "run_dir", lambda t: str(tmp_path))
    monkeypatch.setattr(fworker, "emit_trace_with_span", lambda *a, **k: None)
    monkeypatch.setattr(failure_record, "load_node_map", lambda *a, **k: {})
    monkeypatch.setattr(failure_record, "worktree_origin_check",
                        lambda w, e: {"path": w, "origin": None, "expected_repo": e,
                                      "match": None})
    monkeypatch.setattr(fworker.capability_proposal, "file_denied_row",
                        lambda *a, **k: False)
    # first attempt of MAX_ATTEMPTS: the ordinary path WOULD retry here
    monkeypatch.setattr(fworker, "charged_attempt_count", lambda *a, **k: 1)
    st["retries"] = []
    monkeypatch.setattr(fworker, "do_attempt",
                        lambda a, n: st["retries"].append(n) or "RETRIED")
    return st


def _fail(env, tmp_path, events, classification="KILLED"):
    return fworker.handle_failure(_Args(), 5002, 1, classification, None, events,
                                  str(tmp_path))


def test_classifier_denial_is_frank_gated_on_the_first_attempt(env, tmp_path):
    rc = _fail(env, tmp_path, denial_events())

    assert rc == fworker.EXIT_BLOCKED
    assert env["retries"] == [], "the same action gets the same refusal: never retried"
    assert env["block"] == [], "routed to the decision queue, not blocked"
    verbs = [c[0] for c in env["calls"]]
    assert verbs == ["release", "edit", "decide", "note"], verbs
    edit = env["calls"][1]
    assert edit[1:] == ("5002", "--status", "frank_gated", "--gate-type", "frank_decision")
    decide = env["calls"][2]
    prompt = decide[decide.index("--prompt") + 1]
    assert CMD in prompt and "Unauthorized Access" in prompt
    assert env["row"]["status"] == "pending_review" and env["row"]["claimed_by"] is None
    assert env["recipe"] == [failure_record.TRIAGE_CLASSIFIER_DENIED]
    assert env["ntfy"] and env["ntfy"][0][0] == "foreman FRANK-GATED"
    rec = __import__("json").load(open(os.path.join(str(tmp_path), "failure.json")))
    assert rec["triage"] == "CLASSIFIER_DENIED" and rec["route"] == "frank_gated"
    assert rec["block_class"] == "WORK_BLOCKED"


def test_gate_that_does_not_take_blocks_instead_of_retrying(env, tmp_path):
    env["fail"].add("edit")
    rc = _fail(env, tmp_path, denial_events())

    assert rc == fworker.EXIT_BLOCKED and env["retries"] == []
    assert len(env["block"]) == 1
    assert env["block"][0][1].startswith("CLASSIFIER_DENIED: auto-mode classifier denied 5x")
    assert "decide" not in [c[0] for c in env["calls"]]


def test_failed_decide_leaves_the_row_frank_gated(env, tmp_path):
    env["fail"].add("decide")
    rc = _fail(env, tmp_path, denial_events())

    assert rc == fworker.EXIT_BLOCKED and env["retries"] == [] and env["block"] == []
    assert env["row"]["status"] == "frank_gated"


def test_worker_self_block_keeps_the_ordinary_path(env, tmp_path):
    env["row"].update(status="blocked", block_reason="needs Frank: classifier refused")
    rc = _fail(env, tmp_path, denial_events())

    assert rc == "RETRIED", "unchanged behaviour: this fix only owns claimed tickets"
    assert [c for c in env["calls"] if c[0] in ("edit", "decide")] == []


def test_ordinary_failure_still_retries(env, tmp_path):
    ev = denial_events(times=1, text="grep: /nope: No such file or directory")
    assert _fail(env, tmp_path, ev, classification="CRASH") == "RETRIED"
    assert env["calls"] == [] and env["block"] == []


def test_allowlist_denial_is_not_rerouted(env, tmp_path):
    ev = denial_events(times=5, text="This command requires approval")
    assert _fail(env, tmp_path, ev, classification="PERMISSION_LOOP") == "RETRIED"
    assert env["calls"] == []
