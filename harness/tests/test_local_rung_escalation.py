"""wq#2581 -- a local-rung ticket that exhausts MAX_ATTEMPTS gets handed to the next
non-local rung in its archetype chain instead of being blocked. From research/
LOCAL_RUNG_AUDIT_2576.md: 30d measured, 58/103 local tickets blocked, 0/166 local
sessions all-time classified SUCCESS -- fworker's own retry ladder never walks a local
ticket's model, so it never reached sonnet-5.

Mirrors test_charging.py's `stubbed` fixture (same handle_failure() call shape as the
rung-fault tests there) -- record_terminal_failure and capability_proposal.file_denied_row
are stubbed directly rather than exercised for real, same reasoning: this test is about
the escalate-vs-block DECISION handle_failure makes, not about failure-record shape
(covered by test_block_classification.py) or the capability-denial aq row (covered by
capability_proposal's own tests).
"""
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import pytest  # noqa: E402

import fworker  # noqa: E402
import failure_record  # noqa: E402


class _Args:
    model = "qwen36-local"
    repo = "/nowhere"
    archetype = "mechanical-edit"


def _rec(triage, unreachable_paths=None):
    return ({"block_class": "WORK_BLOCKED", "triage": triage, "summary": "x",
             "unreachable_paths": unreachable_paths or []}, "/tmp/fake-failure.json")


@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    calls = {"release": [], "block": [], "note": [], "ntfy": [], "escalate": []}
    monkeypatch.setattr(fworker, "write_prior_attempt_notes", lambda *a, **k: None)
    monkeypatch.setattr(fworker, "wq_release", lambda t: calls["release"].append(t))
    monkeypatch.setattr(fworker, "wq_block", lambda t, r: calls["block"].append((t, r)))
    monkeypatch.setattr(fworker, "ntfy", lambda *a, **k: calls["ntfy"].append(a))
    monkeypatch.setattr(fworker.capability_proposal, "file_denied_row",
                        lambda *a, **k: False)
    # MAX_ATTEMPTS already exhausted: every case here reaches the terminal-failure path,
    # never do_attempt's fresh-retry branch.
    monkeypatch.setattr(fworker, "charged_attempt_count",
                        lambda *a, **k: fworker.MAX_ATTEMPTS)
    monkeypatch.setattr(fworker, "do_attempt",
                        lambda *a, **k: pytest.fail("exhausted attempts must not retry"))
    return calls


def test_local_ticket_false_complete_escalates_instead_of_blocking(stubbed, monkeypatch,
                                                                    tmp_path):
    monkeypatch.setattr(fworker, "wq_show", lambda t: {"status": "open"})
    monkeypatch.setattr(fworker, "record_terminal_failure",
                        lambda *a, **k: _rec(failure_record.TRIAGE_GENUINE_BUG))
    monkeypatch.setattr(fworker.router, "escalate_off_local",
                        lambda tid, reason: (True, "claude-sonnet-5@low"))

    rc = fworker.handle_failure(_Args(), 5001, 1, "FALSE_COMPLETE", None, [], str(tmp_path))

    assert rc == fworker.EXIT_LOCAL_ESCALATED
    assert stubbed["block"] == [], "an escalated ticket must never also be blocked"
    assert stubbed["ntfy"], "an escalation should still leave a (non-paging) trail"


def test_real_block_triage_never_escalates(stubbed, monkeypatch, tmp_path):
    monkeypatch.setattr(fworker, "wq_show", lambda t: {
        "status": "blocked", "block_reason": "worker decided this needs Frank"})
    monkeypatch.setattr(fworker, "record_terminal_failure",
                        lambda *a, **k: _rec(failure_record.TRIAGE_REAL_BLOCK))
    escalate_calls = []
    monkeypatch.setattr(fworker.router, "escalate_off_local",
                        lambda tid, reason: escalate_calls.append((tid, reason)) or (True, "x"))

    rc = fworker.handle_failure(_Args(), 5002, 1, "REFUSED", None, [], str(tmp_path))

    assert escalate_calls == [], "REAL_BLOCK must never even be offered escalation"
    assert stubbed["block"] == [], "REAL_BLOCK: the worker's own block already stands"
    assert rc == fworker.EXIT_BLOCKED


def test_capability_denied_blocks_not_escalates(stubbed, monkeypatch, tmp_path):
    monkeypatch.setattr(fworker, "wq_show", lambda t: {"status": "open"})
    monkeypatch.setattr(fworker, "record_terminal_failure",
                        lambda *a, **k: _rec(failure_record.TRIAGE_CAPABILITY_DENIED))
    escalate_calls = []
    monkeypatch.setattr(fworker.router, "escalate_off_local",
                        lambda tid, reason: escalate_calls.append((tid, reason)) or (True, "x"))

    rc = fworker.handle_failure(_Args(), 5003, 1, "REFUSED", None, [], str(tmp_path))

    assert escalate_calls == [], "a stronger model cannot get past a policy denial"
    assert len(stubbed["block"]) == 1
    assert rc == fworker.EXIT_BLOCKED


def test_unreachable_cited_path_blocks_not_escalates(stubbed, monkeypatch, tmp_path):
    monkeypatch.setattr(fworker, "wq_show", lambda t: {"status": "open"})
    monkeypatch.setattr(fworker, "record_terminal_failure",
                        lambda *a, **k: _rec(failure_record.TRIAGE_GENUINE_BUG,
                                             unreachable_paths=["/some/cited/path"]))
    escalate_calls = []
    monkeypatch.setattr(fworker.router, "escalate_off_local",
                        lambda tid, reason: escalate_calls.append((tid, reason)) or (True, "x"))

    fworker.handle_failure(_Args(), 5004, 1, "FALSE_COMPLETE", None, [], str(tmp_path))

    assert escalate_calls == [], "an escalated model hits the identical unreachable path"
    assert len(stubbed["block"]) == 1


def test_cloud_ticket_unaffected_by_escalation_path(stubbed, monkeypatch, tmp_path):
    monkeypatch.setattr(fworker, "wq_show", lambda t: {"status": "open"})
    monkeypatch.setattr(fworker, "record_terminal_failure",
                        lambda *a, **k: _rec(failure_record.TRIAGE_GENUINE_BUG))
    escalate_calls = []
    monkeypatch.setattr(fworker.router, "escalate_off_local",
                        lambda tid, reason: escalate_calls.append((tid, reason)) or (True, "x"))
    args = _Args()
    args.model = "claude-sonnet-5"  # not local

    rc = fworker.handle_failure(args, 5005, 1, "FALSE_COMPLETE", None, [], str(tmp_path))

    assert escalate_calls == [], "a cloud ticket was never a candidate for this path at all"
    assert len(stubbed["block"]) == 1
    assert rc == fworker.EXIT_BLOCKED


def test_escalation_declined_falls_through_to_ordinary_block(stubbed, monkeypatch, tmp_path):
    # router.escalate_off_local's own reasons to decline (no cloud rung after local,
    # already escalated once) must still leave the ticket in the SAME state a plain
    # exhaustion always has: blocked, not silently dropped on the floor.
    monkeypatch.setattr(fworker, "wq_show", lambda t: {"status": "open"})
    monkeypatch.setattr(fworker, "record_terminal_failure",
                        lambda *a, **k: _rec(failure_record.TRIAGE_GENUINE_BUG))
    monkeypatch.setattr(fworker.router, "escalate_off_local",
                        lambda tid, reason: (False, "no non-local rung after local"))

    rc = fworker.handle_failure(_Args(), 5006, 1, "FALSE_COMPLETE", None, [], str(tmp_path))

    assert len(stubbed["block"]) == 1
    assert rc == fworker.EXIT_BLOCKED
