"""wq#2817: regression cover for the ISSUE-LOG cross-row pointer checker.

wq#2740 corrected kb/ISSUE-LOG.md row 824 but pointed it at row 834 (an
unrelated judgment) instead of 835, and nothing noticed for two days --
doctrine sends every worker through that pointer. The checker in
ops/wq2817-issue-log-pointer-integrity/probe_pointers.py generalises the find.

These tests run against committed fixture tables, not the live kb file, so they
are re-runnable after ISSUE-LOG.md moves on. FIXTURE_MISDIRECTED is the shape of
the real defect, reduced to three rows.

wq#2877 (judgment on wq#2861) added the second block: the first cut of the checker
matched only the all-lowercase, single-target, bare "see row N" that wq#2740 happened
to mistype, so every other way this log writes a citation was invisible to it.
"""
import importlib.util
import os
import subprocess
import sys

import pytest

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


# ---- wq#2877: the shapes the first cut of the regex could not see ----

def test_a_sentence_initial_capitalized_citation_is_not_invisible():
    """wq#2861's contradiction: "See row N" after a period is ordinary English."""
    out = findings(["| 10 | symptom | cause | fix | It landed. See row 4242 for why |"])
    assert len(out) == 1 and out[0].startswith("DANGLING row 10"), out


def test_an_all_caps_citation_is_not_invisible():
    out = findings(["| 10 | symptom | cause | fix | SEE ROW 4242 |"])
    assert len(out) == 1 and out[0].startswith("DANGLING row 10"), out


def test_per_row_is_a_citation_too_and_can_dangle():
    """29 of the corpus's 34 citations say "per row N", not "see row N"."""
    out = findings(["| 10 | symptom | cause | fix | reopened per row 4242 |"])
    assert len(out) == 1 and out[0].startswith("DANGLING row 10"), out


def test_a_hash_prefixed_target_is_a_citation():
    """The live log writes "per row #687" as well as "per row 687"."""
    out = findings(["| 10 | symptom | cause | fix | per row #4242 |"])
    assert len(out) == 1 and out[0].startswith("DANGLING row 10"), out


def test_every_target_in_a_list_citation_is_checked():
    """"see rows 38/147/148" -- the first cut checked only the head number."""
    out = findings([
        "| 10 | symptom | cause | fix | see rows 11/4242, 4243 and 4244 |",
        "| 11 | other | cause | fix | row 10 above |",
    ])
    assert sorted(out) == [
        "DANGLING row 10 (L1) -> 'see row 4242': no such row",
        "DANGLING row 10 (L1) -> 'see row 4243': no such row",
        "DANGLING row 10 (L1) -> 'see row 4244': no such row",
    ], out


def test_per_row_does_not_raise_one_way():
    """ONE-WAY is a claim about correction pointers. "per row N" means "as row N
    requires" -- applying ONE-WAY to it turns 2 live findings into 31 false ones."""
    assert findings([
        "| 10 | symptom | cause | fix | disabled per row 11 |",
        "| 11 | policy | cause | fix | no unattended installs |",
    ]) == []


def test_a_capitalized_back_reference_still_counts_as_one():
    """The target citing "Row 10 above" is a back-reference; ONE-WAY must not fire."""
    assert findings([
        "| 10 | symptom | cause | fix | see row 11 |",
        "| 11 | correction | cause | fix | Row 10 above closes it |",
    ]) == []


def test_capitalized_citation_is_seen_in_the_live_corpus_at_a_pinned_rev():
    """Acceptance probe from outside the fixture population (wq#2630).

    kb/ISSUE-LOG.md row 80 at kb rev 05ab855 carries a sentence-initial citation of
    row 109. The shipped checker read 3 pointers in that whole file; it must now read
    row 80's. Rev-pinned, so this stays re-runnable after the live log moves on.
    """
    try:
        blob = subprocess.run(
            ["git", "-C", probe.KB_DIR, "show", "05ab855:ISSUE-LOG.md"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:  # kb repo not on this box
        pytest.skip(f"kb repo unavailable: {exc}")
    if blob.returncode != 0:
        pytest.skip("kb rev 05ab855 unavailable")
    found = probe.rows(blob.stdout.splitlines())
    row_80 = [t for _, t in found[80]]
    assert len(row_80) == 1 and "ee row 109" in row_80[0], row_80
    assert probe.targets(probe.CITE_RE, row_80[0]) == {109}
    total = sum(len(probe.targets(probe.CITE_RE, t))
                for v in found.values() for _, t in v)
    assert total >= 34, total  # shipped regex saw 3 of these
