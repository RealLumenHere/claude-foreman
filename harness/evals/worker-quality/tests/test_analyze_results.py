import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analyze_results as ar  # noqa: E402


def make_results(rows):
    """rows: list of (expected_verdict, passed)"""
    return {
        "results": {
            "results": [
                {"vars": {"expected_verdict": exp}, "gradingResult": {"pass": passed}}
                for exp, passed in rows
            ]
        }
    }


def test_summarize_all_correct():
    grades = ar.load_grades(make_results([("done", True), ("rework", True)]))
    summary = ar.summarize(grades)
    assert summary["n_cases"] == 2
    assert summary["accuracy_pct"] == 100.0
    assert summary["false_complete_count"] == 0


def test_false_complete_counted():
    # a rework-labeled case the judge wrongly passed as "done" -> false-complete escape
    grades = ar.load_grades(make_results([
        ("rework", False),  # judge got it wrong -> false-complete
        ("rework", True),
        ("done", True),
    ]))
    summary = ar.summarize(grades)
    assert summary["n_rework_cases"] == 2
    assert summary["false_complete_count"] == 1
    assert summary["false_complete_rate_pct"] == 50.0


def test_false_reject_counted():
    grades = ar.load_grades(make_results([("done", False), ("done", True)]))
    summary = ar.summarize(grades)
    assert summary["false_reject_count"] == 1


def test_empty_results():
    grades = ar.load_grades({"results": {"results": []}})
    summary = ar.summarize(grades)
    assert summary["n_cases"] == 0
    assert summary["accuracy_pct"] is None
    assert summary["false_complete_rate_pct"] is None
