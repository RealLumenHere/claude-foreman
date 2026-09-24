"""wq#2489: a text that misses its contract line is a form failure ('rerun'), never a verdict."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from output_contract import ContractResult, Recommendation, parse_or_reask  # noqa: E402


def test_valid_text_parses_with_zero_reasks():
    calls = []
    r = parse_or_reask("body\nRECOMMENDATION: ADOPT because it fits", Recommendation,
                       reask=lambda e: calls.append(e))
    assert r.status == "ok" and r.value.recommendation == "ADOPT"
    assert r.reasks == 0 and calls == []
    assert r.tail.startswith(" because it fits")


@pytest.mark.parametrize("line,want", [
    ("**RECOMMENDATION: SKIP**", "SKIP"),
    ("## RECOMMENDATION: WATCH", "WATCH"),
    ("> - recommendation: adopt", "ADOPT"),
    ("RECOMMENDATION:**SKIP**", "SKIP"),
    ("  RECOMMENDATION :  watch  ", "WATCH"),
])
def test_markdown_decoration_and_case_tolerated(line, want):
    assert parse_or_reask("x\n" + line + "\n", Recommendation).value.recommendation == want


def test_last_contract_line_wins():
    t = "RECOMMENDATION: ADOPT (draft)\n...\nRECOMMENDATION: SKIP\n"
    assert parse_or_reask(t, Recommendation).value.recommendation == "SKIP"


def test_line_far_past_6000_chars_is_read():
    """The wq#2489 defect: reap sliced notes to 6000 chars and lost a verdict at the end."""
    t = ("filler line\n" * 800) + "RECOMMENDATION: WATCH\n"
    assert len(t) > 9000
    assert parse_or_reask(t, Recommendation).status == "ok"


def test_prose_mention_mid_line_is_not_a_contract_line():
    r = parse_or_reask("my RECOMMENDATION: ADOPT is not supported", Recommendation)
    assert r.status == "rerun"


def test_malformed_reasks_once_with_verbatim_error_then_rerun():
    seen = []

    def reask(err):
        seen.append(err)
        return "still no contract line"

    r = parse_or_reask("no verdict here", Recommendation, reask=reask)
    assert r.status == "rerun" and r.value is None
    assert len(seen) == 1 and "recommendation" in seen[0] and r.error == seen[0]
    assert r.reasks == 1


def test_reask_that_fixes_it_returns_ok():
    r = parse_or_reask("nothing", Recommendation, reask=lambda e: "RECOMMENDATION: SKIP")
    assert r.status == "ok" and r.reasks == 1 and r.value.recommendation == "SKIP"


def test_invalid_token_is_a_form_failure_not_a_verdict():
    r = parse_or_reask("RECOMMENDATION: MAYBE", Recommendation)
    assert r.status == "rerun" and "ADOPT, WATCH, SKIP" in r.error


def test_no_reask_callable_never_raises_and_returns_rerun():
    assert parse_or_reask("", Recommendation).status == "rerun"


def test_reask_infrastructure_failure_is_unavailable_not_rerun():
    r = parse_or_reask("nothing", Recommendation, reask=lambda e: None)
    assert r.status == "unavailable" and r.reasks == 0


def test_max_reasks_zero_never_calls_reask():
    called = []
    r = parse_or_reask("nothing", Recommendation, reask=lambda e: called.append(1), max_reasks=0)
    assert r.status == "rerun" and called == []


def test_result_type():
    assert isinstance(parse_or_reask("RECOMMENDATION: SKIP", Recommendation), ContractResult)
