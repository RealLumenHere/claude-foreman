#!/usr/bin/env python3
"""wq#1300: promptfoo python assertion — scores a judge model's verdict against the
dataset's expected_verdict (see gen_dataset.py). Used as `defaultTest.assert` in
promptfooconfig.yaml so every test case is graded the same way.

promptfoo invokes get_assert(output, context) and, across versions, passes context
either as a dict or an object exposing .vars — handled defensively below rather than
pinning a promptfoo version.
"""
import json


def _vars(context):
    if isinstance(context, dict):
        return context.get("vars", {})
    return getattr(context, "vars", {}) or {}


def _extract_verdict(output):
    """Judge output should be strict JSON {"verdict": "done"|"rework", "reason": "..."}.
    Models sometimes wrap it in prose or a fenced code block; take the last {...} blob."""
    text = output if isinstance(output, str) else json.dumps(output)
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        pass
    start, end = text.rfind("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            pass
    return None


def get_assert(output, context):
    v = _vars(context)
    expected = v.get("expected_verdict")
    parsed = _extract_verdict(output)
    if parsed is None:
        return {
            "pass": False,
            "score": 0.0,
            "reason": f"judge output was not parseable JSON: {str(output)[:200]!r}",
        }
    verdict = parsed.get("verdict")
    if verdict not in ("done", "rework"):
        return {
            "pass": False,
            "score": 0.0,
            "reason": f"judge verdict must be 'done' or 'rework', got {verdict!r}",
        }
    ok = verdict == expected
    return {
        "pass": ok,
        "score": 1.0 if ok else 0.0,
        "reason": f"expected={expected} got={verdict} judge_reason={parsed.get('reason', '')!r}",
    }
