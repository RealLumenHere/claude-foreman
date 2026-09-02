import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grader  # noqa: E402


def ctx(expected):
    return {"vars": {"expected_verdict": expected}}


def test_correct_done_verdict_passes():
    out = json.dumps({"verdict": "done", "reason": "all criteria cited"})
    r = grader.get_assert(out, ctx("done"))
    assert r["pass"] is True
    assert r["score"] == 1.0


def test_correct_rework_verdict_passes():
    out = json.dumps({"verdict": "rework", "reason": "no evidence"})
    r = grader.get_assert(out, ctx("rework"))
    assert r["pass"] is True


def test_false_complete_fails():
    # judge said done, ground truth says it should have been rework
    out = json.dumps({"verdict": "done", "reason": "looks fine"})
    r = grader.get_assert(out, ctx("rework"))
    assert r["pass"] is False
    assert r["score"] == 0.0


def test_false_reject_fails():
    out = json.dumps({"verdict": "rework", "reason": "too strict"})
    r = grader.get_assert(out, ctx("done"))
    assert r["pass"] is False


def test_malformed_json_fails_closed():
    r = grader.get_assert("not json at all", ctx("done"))
    assert r["pass"] is False
    assert "not parseable" in r["reason"]


def test_json_wrapped_in_prose_is_extracted():
    out = 'Sure, here is my verdict:\n{"verdict": "done", "reason": "ok"}\nDone.'
    r = grader.get_assert(out, ctx("done"))
    assert r["pass"] is True


def test_invalid_verdict_value_fails():
    out = json.dumps({"verdict": "maybe", "reason": "unsure"})
    r = grader.get_assert(out, ctx("done"))
    assert r["pass"] is False
    assert "must be" in r["reason"]


def test_context_as_object_with_vars_attr():
    class Ctx:
        vars = {"expected_verdict": "done"}
    out = json.dumps({"verdict": "done", "reason": "ok"})
    r = grader.get_assert(out, Ctx())
    assert r["pass"] is True
