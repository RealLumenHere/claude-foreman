import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rework_convergence as rc  # noqa: E402


def write_attempt(runs_root, ticket, n, classification):
    d = os.path.join(runs_root, ticket)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"attempt-{n}.json"), "w") as f:
        json.dump({"ticket": ticket, "attempt": n, "classification": classification}, f)


def test_converges_at_second_attempt(tmp_path):
    root = str(tmp_path)
    write_attempt(root, "wq1", 1, "FALSE_COMPLETE")
    write_attempt(root, "wq1", 2, "SUCCESS")
    results = rc.scan(root)
    assert len(results) == 1
    assert results[0]["converged_at_attempt"] == 2
    assert results[0]["false_complete_attempts"] == 1


def test_never_converges(tmp_path):
    root = str(tmp_path)
    for n in (1, 2, 3):
        write_attempt(root, "wq2", n, "FALSE_COMPLETE")
    results = rc.scan(root)
    assert results[0]["converged_at_attempt"] is None
    assert results[0]["false_complete_attempts"] == 3


def test_ticket_with_no_false_complete_excluded(tmp_path):
    root = str(tmp_path)
    write_attempt(root, "wq3", 1, "SUCCESS")
    results = rc.scan(root)
    assert results == []


def test_non_ticket_dirs_ignored(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "not-a-ticket-dir"))
    write_attempt(root, "wq4", 1, "FALSE_COMPLETE")
    write_attempt(root, "wq4", 2, "DEGRADED_SUCCESS")
    results = rc.scan(root)
    assert len(results) == 1
    assert results[0]["ticket"] == "wq4"


def test_summarize_convergence_rate(tmp_path):
    root = str(tmp_path)
    write_attempt(root, "wq5", 1, "FALSE_COMPLETE")
    write_attempt(root, "wq5", 2, "SUCCESS")            # converges at 2 (<=2)
    write_attempt(root, "wq6", 1, "FALSE_COMPLETE")
    write_attempt(root, "wq6", 2, "FALSE_COMPLETE")
    write_attempt(root, "wq6", 3, "SUCCESS")             # converges at 3 (>2)
    results = rc.scan(root)
    summary = rc.summarize(results)
    assert summary["tickets_with_false_complete"] == 2
    assert summary["converged_count"] == 2
    assert summary["converged_within_2_attempts"] == 1
    assert summary["convergence_rate_le2_pct"] == 50.0


def test_scan_missing_runs_root_returns_empty(tmp_path):
    assert rc.scan(str(tmp_path / "does-not-exist")) == []
