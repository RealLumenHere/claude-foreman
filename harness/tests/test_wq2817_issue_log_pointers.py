"""wq#2817: regression cover for the ISSUE-LOG cross-row pointer checker.

wq#2740 corrected kb/ISSUE-LOG.md row 824 but pointed it at row 834 (an
unrelated judgment) instead of 835, and nothing noticed for two days --
doctrine sends every worker through that pointer. The checker in
ops/wq2817-issue-log-pointer-integrity/probe_pointers.py generalises the find.

These tests run against committed fixture tables, not the live kb file, so they
are re-runnable after ISSUE-LOG.md moves on. FIXTURE_MISDIRECTED is the shape of
the real defect, reduced to three rows.
"""
import importlib.util
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)  # conftest's autouse fixture imports fworker
REPO = os.path.dirname(HARNESS_DIR)
_spec = importlib.util.spec_from_file_location(
    "probe_pointers",
    os.path.join(REPO, "ops", "wq2817-issue-log-pointer-integrity", "probe_pointers.py"),
)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


# row 824 cites 834; 834 is about an unrelated chain; the row that cites 824
# back is 835. This is wq#2740's error with the prose stripped out.
FIXTURE_MISDIRECTED = """\
| 824 | wq#2733 tripwire fired | committed doc text | no rotation | SETTLED -- every specific is false: see row 834 |
| 834 | wq#2739 judgment on wq#2737 | probe could not fail | probed outside the corpus | FIXED |
| 835 | wq#2740 judgment on wq#2736 | row 824 above closes it on evidence that does not exist | appended this row | SETTLED |
""".splitlines()

FIXTURE_CORRECTED = [
    line.replace("see row 834", "see row 835") for line in FIXTURE_MISDIRECTED
]

FIXTURE_DANGLING = ["| 10 | symptom | cause | fix | see row 4242 |"]

FIXTURE_DUPLICATE = [
    "| 836 | wq#1789 pilot | calibration collapsed | retried | OPEN |",
    "| 836 | wq#2740 same run | five tests fail on main | filed | OPEN |",
]


def findings(lines):
    return probe.check(probe.rows(lines))


def test_misdirected_pointer_is_one_way_and_is_caught():
    out = findings(FIXTURE_MISDIRECTED)
    assert out == [
        "ONE-WAY  row 824 (L1) -> 'see row 834': row 834 never mentions row 824"
    ], out


def test_pointer_at_the_row_that_cites_it_back_passes():
    assert findings(FIXTURE_CORRECTED) == []


def test_pointer_to_a_row_number_that_does_not_exist_is_dangling():
    out = findings(FIXTURE_DANGLING)
    assert len(out) == 1 and out[0].startswith("DANGLING row 10"), out


def test_two_rows_sharing_a_number_make_citations_ambiguous():
    out = findings(FIXTURE_DUPLICATE)
    assert out == ["DUP      row 836 defined 2x (L1, L2)"], out


def test_checker_reports_nothing_on_a_log_with_no_pointers():
    assert findings(["| 1 | symptom | cause | fix | FIXED |"]) == []
