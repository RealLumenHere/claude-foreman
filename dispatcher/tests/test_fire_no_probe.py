"""wq#1261 (#13 item 1, AC3) — "A fire ticket without a probe is refused dispatch by the
router entirely — a fire you can't re-verify is a FRANK page, not a worker."

Reuses test_dispatcher.py's `denv` fixture and helpers (real `wq`/`slock` against a
scratch DB, fake lumen-run/governor/ntfy) rather than re-deriving that scaffolding.
"""
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, os.path.dirname(TESTS_DIR))
import foreman_dispatcher as fd  # noqa: E402
from test_dispatcher import (  # noqa: E402
    denv, add_ticket, log_events, ntfy_lines, lumen_calls, FIRE_PROBE_DETAIL,
)


def _approve(denv, tid):
    (denv["approvals"] / f"wq{tid}.approved").write_text("")


def test_probeless_fire_ticket_refused_dispatch(denv):
    tid = add_ticket("unexplained CRITICAL, no probe attached", lane="fleet-fires")
    _approve(denv, tid)  # approved, so the ONLY thing left to trip is the probe gate
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "fire_no_probe"
    assert log_events(denv, "spawn", ticket=tid) == []
    assert lumen_calls(denv) == []
    assert len(ntfy_lines(denv)) == 1 and f"wq#{tid}" in ntfy_lines(denv)[0]

    # second pass same day: skipped again, but Frank is not re-paged (24h dedup, same
    # ledger discipline as the fire_unapproved gate it sits next to)
    assert fd.run_pass() == 0
    assert len(log_events(denv, "skip", ticket=tid)) == 2
    assert len(ntfy_lines(denv)) == 1


def test_fire_probe_cmd_schema_lets_it_through(denv):
    tid = add_ticket("CRITICAL with a real probe", lane="fleet-fires",
                     detail=FIRE_PROBE_DETAIL)
    _approve(denv, tid)
    assert fd.run_pass() == 0
    assert all(e["reason"] != "fire_no_probe" for e in log_events(denv, "skip", ticket=tid))
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


def test_legacy_aq_row_reference_still_counts_as_a_probe(denv):
    # wq#1234 tickets (aq_escalate.seed_ticket's "Probe: x\naq row: #N" shape) predate
    # the fire_probe schema — they must not suddenly get refused. Single-line here
    # (not aq_escalate's real embedded-newline shape) to sidestep the wq_show() plain-
    # text multi-line-field bug logged separately (ISSUE-LOG.md) — parse_probe_ref's
    # regexes don't require the two markers to be on separate lines.
    detail = "Probe: probe-maat-vault aq row: #967 (source=probe, priority=high)"
    tid = add_ticket("legacy aq-escalated fire", lane="fleet-fires", detail=detail)
    _approve(denv, tid)
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


def test_malformed_fire_probe_line_treated_as_absent(denv):
    tid = add_ticket("garbled probe", lane="fleet-fires",
                     detail='fire_probe: {"cmd": "", "clear_when_exit": 0}')
    _approve(denv, tid)
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "fire_no_probe"
