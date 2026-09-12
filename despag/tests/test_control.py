"""wq#1807: despag P1-4 — control_sqlite.ControlStore: idempotency reserve,
per-session lease + monotonic fence, response terminal latch."""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from despag.backing.control_sqlite import ControlStore


def run(coro):
    return asyncio.run(coro)


# ── idempotency reserve ─────────────────────────────────────────────────────
def test_idem_reserve_first_caller_wins(tmp_path):
    cs = ControlStore(str(tmp_path / "control.db"))
    winner = run(cs.idem_reserve("key1", "resp_a"))
    assert winner is None  # None == we won


def test_idem_reserve_second_caller_reads_winner(tmp_path):
    cs = ControlStore(str(tmp_path / "control.db"))
    assert run(cs.idem_reserve("key1", "resp_a")) is None
    loser = run(cs.idem_reserve("key1", "resp_b"))
    assert loser is not None
    assert loser["resp_id"] == "resp_a"  # loser sees the WINNER's response id


def test_idem_two_threads_race_produce_one_reservation(tmp_path):
    """The acceptance-critical race: two threads submit the same Idempotency-Key
    concurrently. Exactly one must win (spawn); the other must see the winner's
    resp_id and never get its own reservation."""
    db = str(tmp_path / "control.db")
    ControlStore(db)  # create schema before threads race on it
    results: list[dict | None] = [None, None]

    def worker(i: int, resp_id: str):
        cs = ControlStore(db)
        results[i] = run(cs.idem_reserve("race-key", resp_id))

    t1 = threading.Thread(target=worker, args=(0, "resp_thread_0"))
    t2 = threading.Thread(target=worker, args=(1, "resp_thread_1"))
    t1.start(); t2.start()
    t1.join(); t2.join()

    winners = [r for r in results if r is None]
    losers = [r for r in results if r is not None]
    assert len(winners) == 1
    assert len(losers) == 1
    # the single surviving reservation is one of the two submitted resp_ids
    assert losers[0]["resp_id"] in ("resp_thread_0", "resp_thread_1")

    final = ControlStore(db)
    assert run(final.idem_get("race-key"))["resp_id"] == losers[0]["resp_id"]


def test_idem_retention_at_least_24h_default_survives_short_horizon(tmp_path):
    cs = ControlStore(str(tmp_path / "control.db"))
    run(cs.idem_reserve("key1", "resp_a", ttl_s=86400))
    got = run(cs.idem_get("key1"))
    assert got is not None
    assert got["resp_id"] == "resp_a"


def test_idem_expired_reservation_is_reclaimable(tmp_path):
    cs = ControlStore(str(tmp_path / "control.db"))
    run(cs.idem_reserve("key1", "resp_a", ttl_s=0))  # expires immediately
    time.sleep(0.01)
    winner = run(cs.idem_reserve("key1", "resp_b"))
    assert winner is None  # the expired reservation no longer blocks a new one


def test_idem_release_frees_the_key_for_a_retry(tmp_path):
    cs = ControlStore(str(tmp_path / "control.db"))
    run(cs.idem_reserve("key1", "resp_a"))
    run(cs.idem_release("key1", "resp_a"))
    winner = run(cs.idem_reserve("key1", "resp_b"))
    assert winner is None  # released — resp_b is free to reserve it


def test_idem_release_never_clobbers_a_settled_reservation(tmp_path):
    cs = ControlStore(str(tmp_path / "control.db"))
    run(cs.idem_reserve("key1", "resp_a"))
    run(cs.idem_settle("key1", "resp_a", {"status": "completed"}))
    run(cs.idem_release("key1", "resp_a"))  # must be a no-op post-settle
    got = run(cs.idem_get("key1"))
    assert got["state"] == "done"


def test_idem_settle_then_racer_sees_output(tmp_path):
    cs = ControlStore(str(tmp_path / "control.db"))
    run(cs.idem_reserve("key1", "resp_a"))
    run(cs.idem_settle("key1", "resp_a", {"status": "completed"}))
    got = run(cs.idem_get("key1"))
    assert got["state"] == "done"
    assert got["output"] == {"status": "completed"}


# ── per-session lease + monotonic fence ─────────────────────────────────────
def test_lease_admit_first_owner_gets_fence_1(tmp_path):
    cs = ControlStore(str(tmp_path / "control.db"))
    r = run(cs.lease_admit("sid1", "owner-a", ttl_s=60))
    assert r["fence"] == 1
    assert r["conflict"] is False


def test_lease_fence_is_monotonic_across_admits(tmp_path):
    cs = ControlStore(str(tmp_path / "control.db"))
    r1 = run(cs.lease_admit("sid1", "owner-a", ttl_s=60))
    r2 = run(cs.lease_admit("sid1", "owner-b", ttl_s=60))
    assert r2["fence"] == r1["fence"] + 1


def test_lease_fence_mismatch_refuses_commit(tmp_path):
    """The acceptance-critical fence check: owner-a acquires the lease, then
    owner-b re-admits (bumping the fence). owner-a's OLD fence must no longer be
    considered held — its commit is refused."""
    cs = ControlStore(str(tmp_path / "control.db"))
    r1 = run(cs.lease_admit("sid1", "owner-a", ttl_s=60))
    assert run(cs.lease_still_held("sid1", r1["fence"])) is True

    r2 = run(cs.lease_admit("sid1", "owner-b", ttl_s=60))
    assert r2["fence"] != r1["fence"]

    # owner-a's fence is now stale — its commit must be refused.
    assert run(cs.lease_still_held("sid1", r1["fence"])) is False
    # owner-b, who holds the current fence, is still clear to commit.
    assert run(cs.lease_still_held("sid1", r2["fence"])) is True


def test_lease_admit_reject_on_conflict_returns_409_shape(tmp_path):
    cs = ControlStore(str(tmp_path / "control.db"))
    run(cs.lease_admit("sid1", "owner-a", ttl_s=60))
    rejected = run(cs.lease_admit("sid1", "owner-b", ttl_s=60, reject_on_conflict=True))
    assert rejected["rejected"] is True
    assert rejected["holder"] == "owner-a"


def test_lease_admit_reject_on_conflict_does_not_steal_fence(tmp_path):
    cs = ControlStore(str(tmp_path / "control.db"))
    r1 = run(cs.lease_admit("sid1", "owner-a", ttl_s=60))
    run(cs.lease_admit("sid1", "owner-b", ttl_s=60, reject_on_conflict=True))
    # a rejected admit must not have bumped the fence out from under owner-a
    assert run(cs.lease_still_held("sid1", r1["fence"])) is True


def test_lease_admit_same_owner_is_not_a_conflict(tmp_path):
    cs = ControlStore(str(tmp_path / "control.db"))
    run(cs.lease_admit("sid1", "owner-a", ttl_s=60))
    r2 = run(cs.lease_admit("sid1", "owner-a", ttl_s=60, reject_on_conflict=True))
    assert r2["rejected"] is False
    assert r2["conflict"] is False


def test_lease_admit_expired_incumbent_is_not_a_conflict(tmp_path):
    cs = ControlStore(str(tmp_path / "control.db"))
    run(cs.lease_admit("sid1", "owner-a", ttl_s=0))
    time.sleep(0.01)
    r2 = run(cs.lease_admit("sid1", "owner-b", ttl_s=60, reject_on_conflict=True))
    assert r2["rejected"] is False


def test_lease_renew_only_succeeds_for_current_fence(tmp_path):
    cs = ControlStore(str(tmp_path / "control.db"))
    r1 = run(cs.lease_admit("sid1", "owner-a", ttl_s=60))
    assert run(cs.lease_renew("sid1", "owner-a", r1["fence"], ttl_s=60)) is True
    run(cs.lease_admit("sid1", "owner-b", ttl_s=60))  # bumps fence
    assert run(cs.lease_renew("sid1", "owner-a", r1["fence"], ttl_s=60)) is False


def test_lease_release_frees_admission_for_a_follow_up(tmp_path):
    cs = ControlStore(str(tmp_path / "control.db"))
    r1 = run(cs.lease_admit("sid1", "owner-a", ttl_s=60))
    run(cs.lease_release("sid1", "owner-a", r1["fence"]))
    r2 = run(cs.lease_admit("sid1", "owner-b", ttl_s=60, reject_on_conflict=True))
    assert r2["rejected"] is False


# ── response terminal latch ─────────────────────────────────────────────────
def test_resp_put_running_then_read(tmp_path):
    cs = ControlStore(str(tmp_path / "control.db"))
    run(cs.resp_put_running("resp1", "sid1", ttl_s=60))
    got = run(cs.resp_get("resp1"))
    assert got["status"] == "running"


def test_resp_terminal_latch_cannot_go_backwards(tmp_path):
    """The acceptance-critical latch check: once a response is terminal, neither a
    racing resp_put_running nor a second, different terminal status can move it."""
    cs = ControlStore(str(tmp_path / "control.db"))
    run(cs.resp_put_running("resp1", "sid1", ttl_s=60))
    first = run(cs.resp_mark_terminal("resp1", "completed", ttl_s=60))
    assert first["status"] == "completed"

    # a racing "still running" write must not revive it
    run(cs.resp_put_running("resp1", "sid1", ttl_s=60))
    assert run(cs.resp_get("resp1"))["status"] == "completed"

    # a second terminal write (even a different status) must not move it either
    second = run(cs.resp_mark_terminal("resp1", "cancelled", ttl_s=60))
    assert second["status"] == "completed"
    assert run(cs.resp_get("resp1"))["status"] == "completed"


def test_resp_mark_terminal_is_idempotent_for_same_status(tmp_path):
    cs = ControlStore(str(tmp_path / "control.db"))
    run(cs.resp_mark_terminal("resp1", "failed", ttl_s=60))
    again = run(cs.resp_mark_terminal("resp1", "failed", ttl_s=60))
    assert again["status"] == "failed"


def test_resp_mark_terminal_rejects_non_terminal_status(tmp_path):
    cs = ControlStore(str(tmp_path / "control.db"))
    with pytest.raises(ValueError):
        run(cs.resp_mark_terminal("resp1", "running", ttl_s=60))
