"""classify_block_reason() — wq#725 AUTO-7 unit coverage.

Pure-function mapping from a classifier.py verdict to the block-reason class
dispatcher/self_heal.py acts on. No subprocess, no wq — the end-to-end wiring
(handle_failure() actually prefixing wq_block()'s reason) is covered by
test_fworker_integration.py's turn_setup_failed/denied_verb_blocked cases.
"""
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402


def test_infra_crash_classifications():
    for c in ("LAUNCH_FAILURE", "KILLED", "OOM"):
        assert fworker.classify_block_reason(c) == "INFRA_CRASH", c


def test_limit_hit_classifications():
    for c in ("TURNS", "BUDGET"):
        assert fworker.classify_block_reason(c) == "LIMIT_HIT", c


def test_work_blocked_classifications():
    # REFUSED and CRASH are explicit content/ambiguous cases; anything not known to be
    # infra or a limit defaults to WORK_BLOCKED (never auto-requeued without evidence).
    for c in ("REFUSED", "CRASH", "SOME_FUTURE_VERDICT"):
        assert fworker.classify_block_reason(c) == "WORK_BLOCKED", c


def test_permission_loop_is_harness_defect_not_infra_crash():
    # wq#1107: the whole point of the new verdict is that it does NOT land in the
    # INFRA_CRASH bucket — that bucket is both auto-requeued by self_heal (pointless
    # against a static settings gap) and counted by the dispatcher circuit breaker.
    assert fworker.classify_block_reason("PERMISSION_LOOP") == "HARNESS_DEFECT"
    assert "PERMISSION_LOOP" not in fworker._INFRA_CRASH_CLASSIFICATIONS
    assert "PERMISSION_LOOP" not in fworker._LIMIT_HIT_CLASSIFICATIONS
