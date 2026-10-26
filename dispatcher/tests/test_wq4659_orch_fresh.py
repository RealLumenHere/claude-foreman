"""wq#4659: a ticket an orch-* session just filed is the orchestrator's own build.

wq#4645 was created 11:31:15 and spawned 11:32:15; wq#4658 was created 15:30:35 and spawned
15:30:51 (dispatcher.log `spawn` events, 2026-10-26). Both were filed by an orchestrator that
was building them itself. `orch_fresh_until` holds such a ticket for FOREMAN_ORCH_GRACE_MIN
minutes unless it was `wq release`d; the OrchFresh gate turns that into a parked skip.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
from test_dispatcher import fd  # noqa: E402
import gates  # noqa: E402

FMT = "%Y-%m-%d %H:%M:%S"
NOW = datetime(2026, 10, 26, 15, 30, 51, tzinfo=timezone.utc)


def ticket(created_by="orch-opus-1026-a1b2c3", age_s=16, released_at="None"):
    return {"created_by": created_by, "released_at": released_at,
            "created_at": (NOW - timedelta(seconds=age_s)).strftime(FMT)}


def pin_now(monkeypatch):
    monkeypatch.setattr(fd, "_utcnow", lambda: NOW)
    monkeypatch.delenv("FOREMAN_ORCH_GRACE_MIN", raising=False)


def test_fresh_orch_ticket_is_held_until_grace_ends(monkeypatch):
    pin_now(monkeypatch)
    assert fd.orch_fresh_until(ticket()) == "2026-10-26 15:40:35"


def test_non_orch_creator_is_not_held(monkeypatch):
    pin_now(monkeypatch)
    for who in ("None", "", "fworker-wq4658-a1", "lumen:1234", "orchestrator"):
        assert fd.orch_fresh_until(ticket(created_by=who)) is None, who


def test_released_ticket_is_not_held(monkeypatch):
    pin_now(monkeypatch)
    assert fd.orch_fresh_until(ticket(released_at="2026-10-26 15:30:40")) is None


def test_window_expires(monkeypatch):
    pin_now(monkeypatch)
    assert fd.orch_fresh_until(ticket(age_s=599)) is not None
    assert fd.orch_fresh_until(ticket(age_s=600)) is None


def test_env_override_and_rollback(monkeypatch):
    pin_now(monkeypatch)
    monkeypatch.setenv("FOREMAN_ORCH_GRACE_MIN", "0")
    assert fd.orch_fresh_until(ticket()) is None
    monkeypatch.setenv("FOREMAN_ORCH_GRACE_MIN", "30")
    assert fd.orch_fresh_until(ticket(age_s=900)) == "2026-10-26 15:45:51"
    monkeypatch.setenv("FOREMAN_ORCH_GRACE_MIN", "junk")
    assert fd.orch_fresh_until(ticket(age_s=599)) is not None


def test_pre_migration_row_is_not_held(monkeypatch):
    """A row from before the created_by column (or a wq show without it) never holds."""
    pin_now(monkeypatch)
    assert fd.orch_fresh_until({"created_at": NOW.strftime(FMT)}) is None
    assert fd.orch_fresh_until(ticket(age_s=0) | {"created_at": "garbage"}) is None


class _G:
    def __init__(self, t):
        self.fd, self.t = fd, t


def test_gate_parks_with_recheck_at_window_end(monkeypatch):
    pin_now(monkeypatch)
    res = gates.OrchFresh().check(_G(ticket()))
    assert res is not gates.PASS
    assert (res.stage, res.reason) == ("orch_fresh", "orch_fresh")
    assert res.parkable and res.recheck_after == "2026-10-26T15:40:35Z"
    assert set(res.fingerprint_inputs) == {"created_by", "released_at"}
    assert gates.OrchFresh().check(_G(ticket(created_by="None"))) is gates.PASS
