"""wq#1705 — deterministic derivation must not mistake prose for a command.

A criterion is PINNED at dispatch and the worker cannot edit it, so a derivation
that turns an English sentence into a `cmd` hands the lane a gate nothing can
satisfy. wq#1705 was titled "make failed guard services self-heal or alert
instead of staying dead" and got exactly that: AC1 = `make failed guard services
self-heal or alert instead of staying dead`, against a repo with no Makefile.

Zero quota: `_derive_deterministic` takes no model call.
"""
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import acceptance  # noqa: E402

WQ1705_TITLE = "make failed guard services self-heal or alert instead of staying dead"


def _cmds(title, detail=""):
    return [c["cmd"] for c in acceptance._derive_deterministic(
        {"title": title, "detail": detail}) if c.get("proof_type") == "test"]


def test_the_wq1705_title_is_not_derived_as_a_make_target():
    assert _cmds(WQ1705_TITLE) == []


def test_a_prose_line_still_yields_the_citation_criterion():
    """Rejecting the bogus command must not leave the ticket ungated."""
    crit = acceptance._derive_deterministic({"title": WQ1705_TITLE, "detail": ""})
    assert [c["id"] for c in crit] == ["AC1"]
    assert crit[0]["proof_type"] == "citation"


def test_english_verb_openers_are_all_rejected_as_prose():
    for title in ("make the drift guard idempotent",
                  "go and fix the dispatcher stall",
                  "make sure the timer is enabled so it runs",
                  "npm is the wrong tool for this repo"):
        assert _cmds(title) == [], title


def test_a_real_bare_invocation_still_derives():
    assert _cmds("build fix\nmake test") == ["make test"]
    assert _cmds("build fix\npytest -q harness/tests") == ["pytest -q harness/tests"]
    assert _cmds("build fix\nmake lint test") == ["make lint test"]


def test_a_backticked_command_is_trusted_even_when_long():
    """An author who backticks a span is asserting it is a command; only the
    unquoted-line heuristic tightened."""
    detail = "run `pytest -q harness/tests/test_evidence_gate.py -k derivation -x`"
    assert _cmds("some title", detail) == [
        "pytest -q harness/tests/test_evidence_gate.py -k derivation -x"]


def test_looks_like_prose_boundaries():
    assert not acceptance._looks_like_prose(["make", "test"])
    assert not acceptance._looks_like_prose(["make", "a b c d e".replace(" ", "-")])
    assert acceptance._looks_like_prose(["make", "the", "thing"])
    assert acceptance._looks_like_prose("make one two three four five six".split())
