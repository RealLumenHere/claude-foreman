"""fworker.block_reason_class — wq#725. Pure unit coverage of the classification ->
block-reason-prefix mapping, including REFUSED/BUDGET/unknown-string cases that no
fake-claude scenario drives end-to-end (RELIABILITY_SPEC.md §D: REFUSED/BUDGET/CRASH
are coarse catch-alls with no dedicated shim). End-to-end coverage of the mapping for
LAUNCH_FAILURE/KILLED/OOM/TURNS/CRASH lives in test_shim_matrix.py and
test_fworker_integration.py, which assert the actual `wq show` notes field.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fworker import (
    block_reason_class,
    TURNS, LAUNCH_FAILURE, KILLED, OOM, REFUSED, BUDGET, CRASH,
)


def test_infra_crash_classes():
    for classification in (LAUNCH_FAILURE, KILLED, OOM):
        assert block_reason_class(classification) == "INFRA_CRASH"


def test_limit_hit_classes():
    for classification in (TURNS, BUDGET):
        assert block_reason_class(classification) == "LIMIT_HIT"


def test_work_blocked_classes():
    # REFUSED: the worker itself declined the work. CRASH: the spec's unclassified
    # catch-all, no sharper evidence either way — both default to the
    # human-reviewed bucket, never auto-healed.
    for classification in (REFUSED, CRASH):
        assert block_reason_class(classification) == "WORK_BLOCKED"


def test_unknown_classification_defaults_to_work_blocked():
    # §D: no verdict without evidence — an unrecognized string must fall to the most
    # conservative reading, not be silently treated as safe-to-auto-heal.
    assert block_reason_class("SOME_FUTURE_VERDICT") == "WORK_BLOCKED"
