"""wq#1911 ACCEPTANCE: an external SIGTERM arriving in the GAP between attempts (not
while a child is in flight) must still release the wq claim. wq#1908 found that
_SIGTERM_CTX["written"] stays True (stale from the just-finished attempt) across the
whole window between mark_attempt_written() and the next set_sigterm_context() call —
handle_limit()'s up-to-RESUME_WAIT_MAX_S (6h) resume-wait sleep is the concrete,
real-world-exposed instance (fworker.py handle_limit). handle_external_sigterm() used
to see "written=True" and silently no-op, reproducing the wq#1852 "claimed for 6h" bug
that wq#1894 was supposed to have closed.

Unit-level repro matching the one in the ticket body: simulate an attempt having
landed (set_sigterm_context + mark_attempt_written), then simulate handle_limit's
sleep loop (set_sigterm_idle), then fire the signal handler directly and assert
wq_release is actually called and no bogus attempt record is written.
"""
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402


def test_sigterm_during_inter_attempt_gap_releases_claim(monkeypatch):
    released = []
    written = []
    monkeypatch.setattr(fworker, "wq_release", lambda ticket_id: released.append(ticket_id))
    monkeypatch.setattr(fworker, "write_attempt_state",
                         lambda ticket_id, n, state: written.append((ticket_id, n, state)))

    # attempt 1 ran and its own record already landed normally (do_attempt's path).
    fworker.set_sigterm_context(4242, 1, "test-worker")
    fworker.mark_attempt_written()

    # handle_limit() enters its resume-wait sleep: claim is still ours, no child is
    # running, no attempt-N.json is pending.
    fworker.set_sigterm_idle(4242)

    try:
        fworker.handle_external_sigterm(15, None)
    except SystemExit as e:
        assert e.code == 128 + 15
    else:
        raise AssertionError("handle_external_sigterm must sys.exit")

    assert released == [4242], "claim must be released during the inter-attempt gap"
    assert written == [], "no attempt record should be fabricated/overwritten in the idle gap"


def test_sigterm_before_idle_marker_still_no_ops_on_written_attempt(monkeypatch):
    # sanity: without set_sigterm_idle(), a stale written=True from a finished attempt
    # must NOT be (mis)treated as in-flight either — this pins the pre-existing branch
    # untouched by this fix.
    released = []
    monkeypatch.setattr(fworker, "wq_release", lambda ticket_id: released.append(ticket_id))

    fworker.set_sigterm_context(4243, 1, "test-worker")
    fworker.mark_attempt_written()

    try:
        fworker.handle_external_sigterm(15, None)
    except SystemExit:
        pass

    assert released == [], "written=True with no idle marker is the pre-existing no-op branch"
